import AVFoundation
import AppKit
import ApplicationServices
import OmarchyAppleInstallerTrustCore
import OmarchyInstallerUXCore
import ServiceManagement
import TryOmarchyKitClient
import os

/// Try on this Mac: the kit in Contents/Helpers or a newer one it fetched,
/// this app's own permissions, and Try Omarchy's default VM folder.
@MainActor
final class LiveTrialEnvironment: TrialEnvironment {
  private nonisolated static let logger = Logger(
    subsystem: InstallerProductIdentity.appIdentifier, category: "try-kit")
  private let embedded = TrialKitLayout.embedded()
  private let kits: TrialKitUpdater?
  private var client: VMRunClient?
  /// Keeps a fetched kit's folder in place while it runs.
  private var lease: TrialKitLease?
  private var pendingStart: Task<Void, Never>?
  private var startCancelled = false
  private(set) var runsFetchedKit = false
  /// The kit's networking daemon, whose job build-app.sh copies into this
  /// app's Contents/Library/LaunchDaemons.
  private let networkDaemon = SMAppService.daemon(plistName: "dev.tryomarchy.kit.network.plist")

  init(environment: [String: String] = ProcessInfo.processInfo.environment) {
    guard
      let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
        .first
    else {
      kits = nil
      return
    }
    let settings = TrialKitUpdater.Settings.from(environment: environment)
    kits = TrialKitUpdater(
      store: TrialKitStore.under(
        applicationSupport: support, workspace: InstallerBuildProfile.current.workspaceName),
      embedded: embedded, source: TrialKitReleases(base: settings.releases),
      signatures: TrialKitSignatureCheck(
        policy: settings.policy, evaluator: SecurityTrialKitCodeEvaluator(enforcesRevocation: true)),
      startSignatures: TrialKitSignatureCheck(policy: settings.policy),
      network: NWInstallerNetworkPathObserver())
  }

  var isAvailable: Bool { embedded.hasLauncher }

  private func vmRoot() -> URL? { TryOmarchyStorage.defaultRoot() }

  func space() -> TrialSpace { TrialSpace.of(vmRoot()) }

  func vmBytes() async -> Int64 {
    guard let root = vmRoot() else { return 0 }
    return await Task.detached(priority: .utility) {
      TryOmarchyStorage.allocatedBytes(at: root)
    }.value
  }

  func permissions() -> TrialPermissions {
    TrialPermissions(
      accessibility: AXIsProcessTrusted() ? .allowed : .notAsked,
      microphone: Self.state(AVCaptureDevice.authorizationStatus(for: .audio)),
      camera: Self.state(AVCaptureDevice.authorizationStatus(for: .video)))
  }

  /// Asks macOS from this app's own process, which shows macOS's prompt and
  /// lists this app in System Settings, where only the person can turn it on.
  /// System Settings opens once macOS has added the row, as in Try Omarchy.
  func requestAccessibility() {
    guard !AXIsProcessTrusted() else { return }
    // A replaced build leaves a row tied to its old signature, which System
    // Settings shows and turning on doesn't grant to this build. Reset only
    // this app's decision so the prompt registers the code installed now, as
    // Try Omarchy's launcher does.
    Self.resetOwnAccessibilityDecision()
    // The value of kAXTrustedCheckOptionPrompt, which Swift 6 treats as mutable.
    _ = AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
    guard
      let settings = URL(
        string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    else { return }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { NSWorkspace.shared.open(settings) }
  }

  private static func resetOwnAccessibilityDecision() {
    guard let identifier = Bundle.main.bundleIdentifier, !identifier.isEmpty else { return }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/tccutil")
    process.arguments = ["reset", "Accessibility", identifier]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    guard (try? process.run()) != nil else { return }
    process.waitUntilExit()
  }

  func requestMicrophone() async { await Self.request(.audio, settings: "Privacy_Microphone") }

  func requestCamera() async { await Self.request(.video, settings: "Privacy_Camera") }

  /// Checks the Mac, then chooses and checks the kit off the main actor, since
  /// a fetched kit's signatures are checked again at every start. Failures
  /// from then on arrive as events.
  func start(onEvent: @escaping @MainActor (VMRunEvent) -> Void) throws {
    if let failure = VMRunClient.preflight() { throw failure }
    guard let kits else {
      try run(TrialKitChoice(layout: embedded), onEvent: onEvent)
      return
    }
    startCancelled = false
    pendingStart = Task { [weak self] in
      let choice = await Task.detached(priority: .userInitiated) { kits.kitForNextStart() }.value
      guard let self else { return }
      pendingStart = nil
      guard !startCancelled else { return onEvent(.exited(status: 0)) }
      do {
        try run(choice, onEvent: onEvent)
      } catch {
        onEvent(
          .error(
            error as? VMRunError
              ?? VMRunError(code: .launchFailed, message: error.localizedDescription)))
        onEvent(.exited(status: 1))
      }
    }
  }

  private func run(_ choice: TrialKitChoice, onEvent: @escaping @MainActor (VMRunEvent) -> Void)
    throws
  {
    if let refusal = choice.refusal {
      Self.logger.error("Try passed over its fetched kit: \(refusal, privacy: .public)")
    }
    let layout = choice.layout
    let client =
      self.client.flatMap { $0.kit.bundleURL == layout.kit.bundleURL ? $0 : nil }
      ?? VMRunClient(kit: layout.kit)
    self.client = client
    runsFetchedKit = choice.isFetched
    lease = choice.lease
    Self.logger.info(
      "Starting Try with kit \((try? layout.manifest().version.description) ?? "unknown", privacy: .public)"
    )
    do {
      let configuration = try TrialRunConfiguration.make(
        productName: InstallerProductIdentity.appName)
      // The main queue keeps the events in the order the client delivers them.
      try client.start(configuration) {
        [weak self] event in
        DispatchQueue.main.async {
          if case .exited = event { self?.lease = nil }
          onEvent(event)
        }
      }
    } catch {
      lease = nil
      throw error
    }
    if let kits {
      Task.detached(priority: .utility) {
        let outcome = await kits.checkIfDue()
        Self.logger.info("Try kit check: \(String(describing: outcome), privacy: .public)")
      }
    }
  }

  func shutDown() {
    if pendingStart != nil {
      startCancelled = true
    } else {
      client?.shutDown()
    }
  }

  func deleteVM() throws {
    guard
      let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
        .first,
      TrialVMFolder.root(under: support).standardizedFileURL
        == vmRoot()?.standardizedFileURL
    else { throw TrialVMFolder.Refusal.notTheDefaultFolder }
    try TrialVMFolder.trash(under: support)
  }

  func bridgedNetwork() -> TrialBridgedNetwork {
    switch networkDaemon.status {
    case .enabled: .enabled
    case .requiresApproval: .needsApproval
    case .notRegistered, .notFound: .notRegistered
    @unknown default: .notRegistered
    }
  }

  func registerBridgedNetwork() throws { try networkDaemon.register() }

  func openBridgedNetworkApproval() { SMAppService.openSystemSettingsLoginItems() }

  func unregisterBridgedNetwork() async throws { try await networkDaemon.unregister() }

  private static func state(_ status: AVAuthorizationStatus) -> TrialPermissionState {
    switch status {
    case .authorized: .allowed
    case .notDetermined: .notAsked
    default: .refused
    }
  }

  private static func request(_ media: AVMediaType, settings pane: String) async {
    switch AVCaptureDevice.authorizationStatus(for: media) {
    case .notDetermined:
      _ = await AVCaptureDevice.requestAccess(for: media)
    case .authorized:
      break
    default:
      if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
        NSWorkspace.shared.open(url)
      }
    }
  }
}

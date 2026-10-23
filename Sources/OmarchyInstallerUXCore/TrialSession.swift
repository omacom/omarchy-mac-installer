#if os(macOS)
  import Foundation
  import Observation
  import TryOmarchyKitClient

  /// What Try needs from this Mac and the embedded Try Omarchy kit.
  @MainActor
  public protocol TrialEnvironment: AnyObject {
    /// Whether this build embeds the kit. Without it there is no Try.
    var isAvailable: Bool { get }
    /// The space in Try Omarchy's default VM folder and on the volume that
    /// holds it.
    func space() -> TrialSpace
    /// The space the VM's folder takes on disk, or zero without one.
    func vmBytes() async -> Int64
    func permissions() -> TrialPermissions
    func requestAccessibility()
    func requestMicrophone() async
    func requestCamera() async
    func start(onEvent: @escaping @MainActor (VMRunEvent) -> Void) throws
    func shutDown()
    /// Moves the VM in the default folder to the Trash.
    func deleteVM() throws
    /// A finished export waits in the VM's outbox, published when it stops.
    func hasExportInOutbox() -> Bool
    /// The kit's bridged-networking daemon, which this app registers as itself.
    func bridgedNetwork() -> TrialBridgedNetwork
    func registerBridgedNetwork() throws
    /// Login Items in System Settings, where the daemon waits for approval.
    func openBridgedNetworkApproval()
    func unregisterBridgedNetwork() async throws
    /// The running or last start runs a kit fetched after this app was
    /// built, whose bridged networking the registered daemon does not serve.
    var runsFetchedKit: Bool { get }
  }

  /// Where the kit's bridged-networking daemon stands with Service Management.
  public enum TrialBridgedNetwork: Equatable, Sendable {
    case notRegistered
    /// Registered, waiting for approval in Login Items.
    case needsApproval
    case enabled
  }

  public struct TrialSpace: Equatable, Sendable {
    public let requiredBytes: Int64
    public let freeBytes: Int64?
    /// A VM already exists, so starting downloads nothing.
    public let hasVM: Bool

    public init(requiredBytes: Int64, freeBytes: Int64?, hasVM: Bool) {
      self.requiredBytes = requiredBytes
      self.freeBytes = freeBytes
      self.hasVM = hasVM
    }

    public var isEnough: Bool { (freeBytes ?? 0) >= requiredBytes }
  }

  public enum TrialPermissionState: Equatable, Sendable {
    case allowed
    case notAsked
    /// Refused or restricted; only System Settings can change it now.
    case refused
  }

  public struct TrialPermissions: Equatable, Sendable {
    public var accessibility: TrialPermissionState
    public var microphone: TrialPermissionState
    public var camera: TrialPermissionState

    public init(
      accessibility: TrialPermissionState, microphone: TrialPermissionState,
      camera: TrialPermissionState
    ) {
      self.accessibility = accessibility
      self.microphone = microphone
      self.camera = camera
    }

    public var allAllowed: Bool {
      accessibility == .allowed && microphone == .allowed && camera == .allowed
    }
  }

  public struct TrialProgress: Equatable, Sendable {
    public let phase: VMRunPhase
    public let percent: Int?

    public init(phase: VMRunPhase, percent: Int?) {
      self.phase = phase
      self.percent = percent
    }

    public var title: String { PlainLanguage.trialProgressTitle(phase) }
    public var fraction: Double? { percent.map { Double($0) / 100 } }
  }

  public struct TrialFailure: Equatable, Sendable {
    public let code: VMRunErrorCode
    public let headline: String
    public let detail: String
    public let technicalDetail: String?

    /// Starting again can help, unlike a Mac or a folder that can't run it.
    public var canTryAgain: Bool {
      switch code {
      case .volumeNotAPFS, .macOSTooOld, .hypervisorUnavailable, .storageLayoutUnknown: false
      default: true
      }
    }
  }

  public enum TrialPhase: Equatable, Sendable {
    /// The installer's own page: the choice, the plan, the install.
    case hidden
    /// The Try page: permissions and space, before anything starts.
    case ready(TrialSpace, TrialPermissions)
    case starting(TrialProgress)
    /// Omarchy runs in its own window, and the installer's window stands
    /// back. A permission granted now applies at the next start.
    case running(TrialPermissions)
    case stopping
    case failed(TrialFailure)
  }

  /// What the installer found it can do on this Mac, which decides what the
  /// landing page's Install button offers.
  public enum TrialInstallStatus: Equatable, Sendable {
    case checking
    /// Install is on offer.
    case supported
    /// Omarchy can't be installed on this Mac.
    case unsupported(FailureDisplay)
    /// Omarchy is installed natively.
    case installed
    /// An install waits for its Recovery step, whose steps take the page.
    case finishingInstall
    /// The installer's own page has the floor: the plan, the install or a
    /// failure.
    case installerPage

    public init(_ phase: InstallerSessionPhase, isBusy: Bool, pendingRecovery: Bool) {
      switch phase {
      case .inspecting:
        self = .checking
      case .welcome:
        self = .supported
      case .unsupported(let failure):
        self = .unsupported(failure)
      case .existingInstallRefused:
        // The inspection is still looking for an unfinished Recovery step.
        if isBusy {
          self = .checking
        } else {
          self = pendingRecovery ? .finishingInstall : .installed
        }
      default:
        self = .installerPage
      }
    }
  }

  extension InstallerSession {
    public var trialInstallStatus: TrialInstallStatus {
      TrialInstallStatus(
        phase, isBusy: isBusy, pendingRecovery: recoveryAccount?.target != nil)
    }
  }

  /// Why Install on this Mac can't go ahead: one line under the landing
  /// page's button, and the details behind it and behind the menu item.
  public struct TrialInstallReason: Equatable, Sendable {
    public let line: String
    public let headline: String
    public let detail: String
    public let canCheckAgain: Bool
  }

  /// The landing page: Install on this Mac, or Migrate to Native once a VM
  /// exists, on the left, and Try in a VM on the right.
  public struct TrialLanding: Equatable, Sendable {
    public enum Install: Equatable, Sendable {
      case checking
      case available
      case unavailable(TrialInstallReason)
    }

    public let install: Install
    public let installTitle: String
    public let tryTitle: String
    /// What Try has to say above the buttons once a VM exists: that it is
    /// ready, or where its folder is.
    public let tryLine: String?
    /// The space free for the VM, once one exists, or the space a new one
    /// still lacks.
    public let spaceLine: String?
    public let spaceIsShort: Bool
    /// Try is the likelier next step: a VM exists, or Install isn't on offer.
    public let tryIsPrimary: Bool

    /// The line above the buttons when neither button has anything to say.
    public var choiceLine: String? {
      install == .available && tryLine == nil ? PlainLanguage.trialLandingChoice : nil
    }
  }

  public enum TrialInstallMenuAction: Equatable, Sendable {
    case unavailable
    case install
    case explain(TrialInstallReason)
  }

  /// Try as one state machine beside the installer's session. Omarchy runs in
  /// its own window; this tracks it, shuts it down and deletes its VM.
  @MainActor
  @Observable
  public final class TrialSession {
    public private(set) var phase: TrialPhase = .hidden
    public private(set) var installStatus: TrialInstallStatus = .checking
    /// A VM exists where Try Omarchy keeps it: the remembered choice.
    public private(set) var hasVM = false
    /// This start asked for bridged networking and got shared networking.
    public private(set) var bridgedNetworkUnavailable = false
    public private(set) var bridgedNetwork: TrialBridgedNetwork = .notRegistered
    /// Why setting up, repairing or removing bridged networking failed.
    public private(set) var bridgedNetworkFailure: String?
    /// Setting up, repairing or removing bridged networking is under way.
    public private(set) var bridgedNetworkChanging = false
    /// The space the VM takes, for the plan review.
    public private(set) var vmBytes: Int64 = 0
    /// The installer's window. It closes once Omarchy's own window is up,
    /// and comes back when Omarchy shuts down or fails, or when the person
    /// asks for it.
    public private(set) var showsWindow = true

    /// The app keeps its Dock tile while its window shows. While Omarchy
    /// runs, Omarchy's window is the one Dock tile, as in Try Omarchy.
    public var showsInDock: Bool { showsWindow }

    /// Closing the last window quits the app, except while Omarchy starts,
    /// runs or shuts down. Try closes the window itself then, and that
    /// must never quit or shut Omarchy down.
    public var quitsWhenTheLastWindowCloses: Bool { !isActive }
    private let environment: any TrialEnvironment
    private var lastError: VMRunError?
    private var shutdownRequested = false
    private var afterExit: [() -> Void] = []
    private var launched = false
    private var vmBytesRefresh: Task<Void, Never>?
    /// Migrate to Native's sheet, which brings an export of the VM's home
    /// into the install.
    public private(set) var showsMigrationNotice = false
    /// Omarchy holds a finished export that its shutdown publishes.
    public private(set) var exportWaitsInOutbox = false
    /// The space last read, for the landing page.
    private var lastSpace: TrialSpace?
    private var pendingInstall: (() -> Void)?

    public init(environment: any TrialEnvironment) {
      self.environment = environment
      guard environment.isAvailable else { return }
      let space = environment.space()
      hasVM = space.hasVM
      lastSpace = space
      bridgedNetwork = environment.bridgedNetwork()
    }

    public var isAvailable: Bool { environment.isAvailable }

    /// Omarchy is starting, running or shutting down.
    public var isActive: Bool {
      switch phase {
      case .starting, .running, .stopping: true
      case .hidden, .ready, .failed: false
      }
    }

    /// Shut Down in the menu, while Omarchy starts or runs.
    public var canShutDown: Bool {
      switch phase {
      case .starting, .running: true
      case .hidden, .ready, .stopping, .failed: false
      }
    }

    public var canStart: Bool {
      guard case .ready(let space, _) = phase else { return false }
      return space.isEnough
    }

    public var startTitle: String {
      guard case .ready(let space, _) = phase, space.hasVM else {
        return PlainLanguage.trialDownloadAndLaunch
      }
      return PlainLanguage.trialLaunch
    }

    /// Install on this Mac, or Migrate to Native, beside Shut Down on the
    /// running page.
    public var canInstall: Bool { installStatus == .supported }

    /// Install on this Mac, or Migrate to Native once a VM exists.
    public var installTitle: String {
      hasVM ? PlainLanguage.trialMigrate : PlainLanguage.trialInstall
    }

    public var installMenuTitle: String {
      hasVM ? PlainLanguage.trialMigrateMenuItem : PlainLanguage.trialInstallMenuItem
    }

    /// The landing page is the first page, unless Try or the installer's own
    /// page has the floor.
    public var showsLanding: Bool {
      environment.isAvailable && phase == .hidden && !installerNeedsPage
    }

    public var landing: TrialLanding {
      let install: TrialLanding.Install
      if installStatus == .supported {
        install = .available
      } else if let installReason {
        install = .unavailable(installReason)
      } else {
        install = .checking
      }
      return TrialLanding(
        install: install, installTitle: installTitle, tryTitle: PlainLanguage.trialEntry,
        tryLine: hasVM ? PlainLanguage.trialLandingVMLine : nil,
        spaceLine: lastSpace.flatMap(PlainLanguage.trialLandingSpace),
        spaceIsShort: lastSpace?.isEnough == false,
        tryIsPrimary: hasVM || install != .available)
    }

    /// Why this Mac can't install, once the inspection says so.
    public var installReason: TrialInstallReason? {
      switch installStatus {
      case .unsupported(let failure): PlainLanguage.trialInstallReason(failure)
      case .installed: PlainLanguage.trialInstalledReason
      case .checking, .supported, .finishingInstall, .installerPage: nil
      }
    }

    public var canDeleteVM: Bool { environment.isAvailable && hasVM && !isActive }

    /// The Try page's line under its headline.
    public var subheadline: String {
      guard case .ready(let space, _) = phase, space.hasVM else {
        return PlainLanguage.trialSubheadline
      }
      return PlainLanguage.trialReadySubheadline
    }

    /// The plan review's note on the VM, which the native install leaves in
    /// place.
    public var diskNotice: String? { PlainLanguage.trialDiskNotice(bytes: vmBytes) }

    /// Try Again on a failure that starting again can help.
    public var canTryAgain: Bool {
      guard case .failed(let failure) = phase else { return false }
      return failure.canTryAgain && !installerNeedsPage
    }

    /// Omarchy started before the inspection found an install waiting for its
    /// Recovery step. The steps appear once Omarchy shuts down.
    public var finishInstallAfterShutDown: Bool {
      installStatus == .finishingInstall && isActive
    }

    public var installMenuAction: TrialInstallMenuAction {
      if installStatus == .supported { return .install }
      return installReason.map { .explain($0) } ?? .unavailable
    }

    /// The remembered choice: with a VM, the app opens on the Try page, with
    /// Allow and Launch, while the installer inspects the Mac in the
    /// background. Back leads to the landing page, which shows at once
    /// without a VM. Nothing starts until the person presses Launch. Runs once
    /// per launch.
    public func openAtLaunch() {
      guard !launched else { return }
      launched = true
      guard environment.isAvailable, phase == .hidden else { return }
      let space = environment.space()
      noteVM(space)
      guard space.hasVM else { return }
      phase = .ready(space, environment.permissions())
    }

    /// Follows the installer's inspection. The installer's plan, install and
    /// Recovery steps take the page whenever Omarchy isn't running.
    public func follow(_ status: TrialInstallStatus) {
      installStatus = status
      guard environment.isAvailable, !isActive, installerNeedsPage else { return }
      phase = .hidden
    }

    /// Try in a VM: the Try page, with its permissions and space.
    public func present() {
      guard environment.isAvailable, phase == .hidden else { return }
      phase = readyPhase()
    }

    /// Back: from a failure to the Try page, or to the installer's page when
    /// it needs the floor, and from the Try page to the landing page.
    public func dismiss() {
      switch phase {
      case .failed:
        phase = installerNeedsPage ? .hidden : readyPhase()
      case .ready:
        phase = .hidden
      default:
        break
      }
    }

    /// Try Again: back to the Try page with the space read again, and start.
    public func tryAgain() {
      guard canTryAgain else { return }
      phase = readyPhase()
      start()
    }

    /// Re-reads permissions and space, as when the app becomes active again
    /// or System Settings closes.
    public func refresh() {
      if environment.isAvailable { bridgedNetwork = environment.bridgedNetwork() }
      switch phase {
      case .ready:
        phase = readyPhase()
      case .running:
        phase = .running(environment.permissions())
      default:
        if !isActive, environment.isAvailable { noteVM(environment.space()) }
      }
    }

    /// The Dock icon or the Window menu brings the window back, with the
    /// page read again.
    public func reopenWindow() {
      showsWindow = true
      refresh()
    }

    public func refreshVMBytes() async {
      vmBytes = await environment.vmBytes()
    }

    /// Waits for the last refresh of the VM's size, for tests.
    func settled() async {
      await vmBytesRefresh?.value
    }

    private func noteVM(_ space: TrialSpace) {
      lastSpace = space
      hasVM = space.hasVM
      guard hasVM else { return }
      vmBytesRefresh = Task { await refreshVMBytes() }
    }

    public func requestAccessibility() {
      environment.requestAccessibility()
      refresh()
    }

    public func requestMicrophone() async {
      await environment.requestMicrophone()
      refresh()
    }

    public func requestCamera() async {
      await environment.requestCamera()
      refresh()
    }

    public func start() {
      guard canStart else { return }
      lastError = nil
      shutdownRequested = false
      bridgedNetworkUnavailable = false
      phase = .starting(TrialProgress(phase: .checking, percent: nil))
      showsWindow = true
      do {
        try environment.start { [weak self] event in self?.receive(event) }
      } catch {
        phase = .failed(Self.failure(for: error))
      }
    }

    /// Shuts Omarchy down; `then` runs once it has stopped, or at once when
    /// nothing is running.
    public func shutDown(then completion: (() -> Void)? = nil) {
      guard isActive else {
        completion?()
        return
      }
      if let completion { afterExit.append(completion) }
      showsWindow = true
      guard !shutdownRequested else { return }
      shutdownRequested = true
      phase = .stopping
      environment.shutDown()
    }

    /// Install on this Mac: shuts Omarchy down if it runs, leaves Try, then
    /// continues into the installer's plan.
    public func leaveForInstall(then install: @escaping () -> Void) {
      guard canInstall else { return }
      shutDown { [weak self] in
        guard let self else { return }
        phase = .hidden
        install()
      }
    }

    /// The left button and the menu item. Once a VM exists they read Migrate
    /// to Native, which first offers to bring an export of the VM's home.
    public func chooseInstall(then install: @escaping () -> Void) {
      guard canInstall else { return }
      guard hasVM else {
        leaveForInstall(then: install)
        return
      }
      pendingInstall = install
      exportWaitsInOutbox = isActive && environment.hasExportInOutbox()
      showsMigrationNotice = true
      showsWindow = true
    }

    /// Shut Down Omarchy, while an export waits in the VM: the launcher
    /// publishes it as Omarchy stops, and `then` runs once it has.
    public func shutDownToPublishExport(then completion: @escaping () -> Void) {
      guard showsMigrationNotice, exportWaitsInOutbox, isActive else { return }
      shutDown { [weak self] in
        guard let self else { return }
        exportWaitsInOutbox = false
        completion()
      }
    }

    /// Install with These Files, or Install Without Migrating: the VM stays
    /// where it is, and the install goes ahead as Install on this Mac does.
    public func continueToInstall() {
      guard showsMigrationNotice, let install = pendingInstall else { return }
      dismissMigrationNotice()
      leaveForInstall(then: install)
    }

    public func dismissMigrationNotice() {
      showsMigrationNotice = false
      exportWaitsInOutbox = false
      pendingInstall = nil
    }

    /// The confirmation's words, naming the space the VM takes.
    public func deletionMessage() async -> String {
      await refreshVMBytes()
      return PlainLanguage.trialDeleteMessage(bytes: vmBytes)
    }

    /// Moves the VM to the Trash and returns to the landing page.
    public func deleteVM() throws {
      guard canDeleteVM else { return }
      try environment.deleteVM()
      noteVM(environment.space())
      vmBytes = 0
      phase = .hidden
    }

    /// The running page's line about bridged networking, while this start
    /// fell back to shared networking.
    public var bridgedNetworkLine: String? {
      guard bridgedNetworkUnavailable, isActive else { return nil }
      if environment.runsFetchedKit { return PlainLanguage.trialBridgedNetworkFetchedKitLine }
      return bridgedNetworkFailure ?? PlainLanguage.trialBridgedNetworkLine(bridgedNetwork)
    }

    /// The line's button. A fetched kit's bridged networking can't be set
    /// up or repaired yet, so it has none.
    public var bridgedNetworkActionTitle: String? {
      guard !environment.runsFetchedKit, !bridgedNetworkChanging else { return nil }
      return PlainLanguage.trialBridgedNetworkAction(bridgedNetwork)
    }

    /// Remove in the menu, once the daemon is registered and nothing is
    /// shutting down or changing it.
    public var canChangeBridgedNetwork: Bool {
      environment.isAvailable && bridgedNetwork != .notRegistered && phase != .stopping
        && !bridgedNetworkChanging
    }

    /// Repair in the menu, which a running fetched kit can't use.
    public var canRepairBridgedNetwork: Bool {
      canChangeBridgedNetwork && !(isActive && environment.runsFetchedKit)
    }

    /// The confirmation's words before Repair or Remove, naming the shutdown
    /// when Omarchy runs.
    public func bridgedNetworkChangeMessage(removing: Bool) -> String {
      PlainLanguage.trialBridgedNetworkChangeMessage(removing: removing, shutsDown: isActive)
    }

    /// Registers the daemon, or opens Login Items while it waits for approval.
    /// A daemon that is set up but still unavailable is repaired. The next
    /// start uses it.
    public func setUpBridgedNetwork() async {
      guard !bridgedNetworkChanging, bridgedNetworkActionTitle != nil else { return }
      bridgedNetworkFailure = nil
      switch environment.bridgedNetwork() {
      case .notRegistered:
        do {
          try environment.registerBridgedNetwork()
        } catch {
          if environment.bridgedNetwork() == .notRegistered {
            bridgedNetworkFailure = PlainLanguage.trialBridgedNetworkFailed(
              error.localizedDescription)
          }
        }
        bridgedNetwork = environment.bridgedNetwork()
        if bridgedNetwork == .needsApproval { environment.openBridgedNetworkApproval() }
      case .needsApproval:
        bridgedNetwork = .needsApproval
        environment.openBridgedNetworkApproval()
      case .enabled:
        await repairBridgedNetwork()
      }
    }

    /// Shuts Omarchy down, then registers the daemon again. Returns why it
    /// failed: Omarchy is down by then, so the running page can't say it.
    @discardableResult
    public func repairBridgedNetwork() async -> String? {
      guard !(isActive && environment.runsFetchedKit) else { return nil }
      let failure = await changeBridgedNetwork { environment in
        try await environment.unregisterBridgedNetwork()
        try environment.registerBridgedNetwork()
      }
      if bridgedNetwork == .needsApproval { environment.openBridgedNetworkApproval() }
      return failure
    }

    /// Shuts Omarchy down, then unregisters the daemon. Returns why it failed.
    @discardableResult
    public func removeBridgedNetwork() async -> String? {
      await changeBridgedNetwork { environment in try await environment.unregisterBridgedNetwork() }
    }

    private func changeBridgedNetwork(
      _ change: @MainActor (any TrialEnvironment) async throws -> Void
    ) async -> String? {
      guard !bridgedNetworkChanging else { return nil }
      bridgedNetworkChanging = true
      defer { bridgedNetworkChanging = false }
      bridgedNetworkFailure = nil
      await withCheckedContinuation { stopped in shutDown { stopped.resume() } }
      var reason: String?
      do {
        try await change(environment)
      } catch {
        reason = error.localizedDescription
        bridgedNetworkFailure = PlainLanguage.trialBridgedNetworkFailed(error.localizedDescription)
      }
      bridgedNetwork = environment.bridgedNetwork()
      return reason
    }

    func receive(_ event: VMRunEvent) {
      switch event {
      case .notice(let notice):
        if notice.code == .bridgedNetworkUnavailable {
          bridgedNetworkUnavailable = true
          bridgedNetwork = environment.bridgedNetwork()
        }
      case .progress(let step, let percent):
        if case .starting = phase {
          phase = .starting(TrialProgress(phase: step, percent: percent))
        }
      case .ready:
        noteVM(environment.space())
        if !shutdownRequested {
          phase = .running(environment.permissions())
          if !showsMigrationNotice { showsWindow = false }
        }
      case .stopping:
        phase = .stopping
        showsWindow = true
      case .error(let error):
        lastError = error
      case .exited(let status):
        if let lastError {
          phase = .failed(Self.failure(for: lastError))
        } else if status != 0 && !shutdownRequested {
          phase = .failed(
            Self.failure(
              for: VMRunError(code: .qemuExited, message: "Omarchy stopped (status \(status)).")))
        } else {
          phase = restingPhase()
        }
        showsWindow = true
        lastError = nil
        shutdownRequested = false
        let completions = afterExit
        afterExit = []
        for completion in completions { completion() }
        vmBytesRefresh = Task { await refreshVMBytes() }
      }
    }

    private func readyPhase() -> TrialPhase {
      let space = environment.space()
      noteVM(space)
      return .ready(space, environment.permissions())
    }

    /// Where Try rests once Omarchy has shut down: the Try page, with Launch
    /// and Back, unless the installer's page needs the floor.
    private func restingPhase() -> TrialPhase {
      installerNeedsPage ? .hidden : readyPhase()
    }

    /// The Recovery steps of an unfinished install, or the installer's plan,
    /// take the page whenever Omarchy isn't running.
    private var installerNeedsPage: Bool {
      installStatus == .finishingInstall || installStatus == .installerPage
    }

    static func failure(for error: Error) -> TrialFailure {
      let runError =
        error as? VMRunError ?? VMRunError(code: .launchFailed, message: error.localizedDescription)
      let words = PlainLanguage.trialFailure(runError.code)
      return TrialFailure(
        code: runError.code, headline: words.headline, detail: words.detail,
        technicalDetail: runError.message)
    }
  }
#endif

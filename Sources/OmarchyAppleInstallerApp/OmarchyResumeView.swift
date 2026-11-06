import AppKit
import OmarchyAppleInstallerTrustCore
import OmarchyInstallerUXCore
import SwiftUI

struct OmarchyResumeView: View {
  let onBusyChanged: (Bool) -> Void
  let onClose: () -> Void
  let encrypt: Bool
  let lane: String
  @State private var ticket: InterruptedInstallResumeTicket?
  @State private var username = NSUserName()
  @State private var password = ""
  @State private var folder: URL?
  @State private var busy = false
  @State private var submitted = false
  @State private var completed = false
  @State private var message =
    "Check the saved installation before resuming. An administrator account is required."
  private static let verifyingMessage =
    "Checking the original download and existing Linux images before rebuilding startup files. Keep this Mac awake and connected to power."

  init(
    onBusyChanged: @escaping (Bool) -> Void, onClose: @escaping () -> Void,
    encrypt: Bool, lane: String
  ) {
    self.onBusyChanged = onBusyChanged
    self.onClose = onClose
    self.encrypt = encrypt
    self.lane = lane
  }

  #if DEBUG
    init(
      previewTicket: InterruptedInstallResumeTicket?, completed: Bool, busy: Bool = false,
      message: String? = nil
    ) {
      self.init(onBusyChanged: { _ in }, onClose: {}, encrypt: true, lane: "stable")
      _ticket = State(initialValue: previewTicket)
      _completed = State(initialValue: completed)
      _busy = State(initialValue: busy)
      _submitted = State(initialValue: busy)
      _username = State(initialValue: "owner")
      _folder = State(
        initialValue: URL(
          fileURLWithPath: "/Downloads/omarchy-mac-4.5.0-r2-2026.11.05-08c81abcef4f"))
      if let message {
        _message = State(initialValue: message)
      } else if busy {
        _message = State(initialValue: Self.verifyingMessage)
      } else if completed {
        _message = State(
          initialValue:
            "Installation is ready for final setup in Recovery. Shut down, then hold the power button until startup options appear and select Omarchy."
        )
      }
    }
  #endif

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      ScrollView {
        VStack(alignment: .leading, spacing: 16) {
          Text(completed ? "Ready for final setup" : "Resume interrupted installation")
            .font(.title2.bold())
          Text(message).textSelection(.enabled)
          if let ticket, !completed {
            Text(
              "Omarchy allocation: \(ByteCountFormatter.string(fromByteCount: Int64(ticket.allocationBytes), countStyle: .decimal))"
            )
            Text(
              "The existing Linux images will be verified. The interrupted startup files will be rebuilt inside this allocation. The macOS partition will not be resized."
            )
          }
        }
      }
      .frame(maxHeight: 240)
      if ticket != nil, !completed {
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            Text(folder?.lastPathComponent ?? "Original download folder required")
              .lineLimit(1).truncationMode(.middle)
            Spacer()
            Button("Choose Folder…", action: chooseFolder).layoutPriority(1)
          }
          Text(
            encrypt
              ? "Omarchy encryption will be enabled."
              : "This private test build installs without encryption."
          )
          .font(.caption).foregroundStyle(.secondary)
        }
        .disabled(busy)
      }
      if busy {
        HStack {
          ProgressView().controlSize(.small)
          Text(
            submitted
              ? "Verifying and resuming. This can take several minutes…"
              : "Checking installation records…")
        }
      }
      if !completed {
        VStack(alignment: .leading, spacing: 8) {
          Text("Administrator account").font(.caption)
          TextField("macOS administrator account", text: $username)
            .textContentType(.username)
          SecureField("macOS password", text: $password)
            .textContentType(.password).privacySensitive()
        }
        .disabled(busy)
      }
      HStack {
        Button(completed ? "Close" : "Cancel", action: onClose).disabled(busy)
        Spacer()
        if !completed {
          Button("Check Installation") { Task { await prepare() } }
            .disabled(busy || username.isEmpty || password.isEmpty)
          Button("Resume Installation") { Task { await resume() } }
            .disabled(busy || submitted || ticket == nil || folder == nil || password.isEmpty)
        }
      }
    }
    .textFieldStyle(.roundedBorder)
    .padding(24).frame(width: 540)
    .interactiveDismissDisabled(busy)
    .onChange(of: busy) { _, value in onBusyChanged(value) }
  }

  private func chooseFolder() {
    let panel = NSOpenPanel()
    panel.canChooseFiles = false
    panel.canChooseDirectories = true
    panel.allowsMultipleSelection = false
    panel.prompt = "Use Download Folder"
    panel.message =
      "Choose the original Omarchy download folder. Its files will be checked against the saved installation record."
    if let folder { panel.directoryURL = folder }
    if panel.runModal() == .OK { folder = panel.url }
  }

  @MainActor private func prepare() async {
    guard !busy else { return }
    busy = true
    submitted = false
    ticket = nil
    message = "Checking the saved installation…"
    defer { busy = false }
    do {
      guard try !AppleSiliconHostInspector().isBlockedModel() else {
        message = "Resuming installation is not supported on this Mac model."
        return
      }
      let authorization = try MachineOwnerAuthorization(
        username: username, password: Data(password.utf8))
      try await InstallerHelperSetup.ensure(authorization)
      let reply = try await InstallerHelperSetup.submitter().prepareInterruptedResume()
      ticket = reply.ticket
      message = reply.message
      if folder == nil, ticket != nil {
        let cache = FileManager.default.urls(
          for: .applicationSupportDirectory, in: .userDomainMask)[0]
          .appendingPathComponent(InstallerProductIdentity.appIdentifier + "/staging")
        let folders = try? FileManager.default.contentsOfDirectory(
          at: cache,
          includingPropertiesForKeys: [.contentModificationDateKey])
        folder = folders?.sorted { $0.lastPathComponent > $1.lastPathComponent }.first {
          FileManager.default.fileExists(
            atPath: $0.appendingPathComponent("installer_data.json").path)
        }
      }
    } catch {
      password = ""
      message = "Could not check the interrupted installation. \(error.localizedDescription)"
    }
  }

  @MainActor private func resume() async {
    guard !busy, !submitted, let ticket, let folder else { return }
    busy = true
    submitted = true
    message = Self.verifyingMessage
    defer {
      busy = false
      password = ""
    }
    do {
      let authorization = try MachineOwnerAuthorization(
        username: username, password: Data(password.utf8))
      let entries = try FileManager.default.contentsOfDirectory(
        at: folder, includingPropertiesForKeys: nil)
      let packages = entries.filter { $0.lastPathComponent.hasSuffix("-os-package.zip") }
      guard packages.count == 1 else {
        throw CocoaError(.fileReadNoSuchFile)
      }
      let metadata = try FileHandle(
        forReadingFrom: folder.appendingPathComponent("installer_data.json"))
      defer { try? metadata.close() }
      let payload = try FileHandle(forReadingFrom: packages[0])
      defer { try? payload.close() }
      let conf = try InstallConf(encrypt: encrypt, lane: lane)
      let reply = try await InstallerHelperSetup.submitter().resumeInterruptedInstall(
        ticket: ticket, metadata: metadata, payload: payload, conf: conf,
        authorization: authorization)
      completed = reply.completed
      message = reply.message
      self.ticket = nil
    } catch {
      if case .engineFailed(let notice) = error as? EngineXPCSubmissionError {
        EngineFailureUserLog.write(notice, operation: "resume-interrupted-install")
      }
      message =
        "Resumption stopped. The installation record was preserved. Check Installation again before retrying. \(Self.failureDetail(error))"
    }
  }

  nonisolated static func failureDetail(_ error: any Error) -> String {
    switch error as? EngineXPCSubmissionError {
    case .engineFailed(let notice):
      return notice.summary.isEmpty
        ? "The engine exited with status \(notice.exitStatus)."
        : notice.summary
    case .helperRejected(let domain, let code):
      return "The installation service refused the request (\(domain), code \(code))."
    default:
      return error.localizedDescription
    }
  }
}

#if os(macOS)
  import Foundation

  /// What Migrate to Native's sheet offers, from what the installer found in
  /// Try Omarchy's Exports folder and in the VM's outbox
  /// (try-omarchy docs/native-install-export.md).
  public enum TrialMigrationOffer: Equatable, Sendable {
    /// Exports hasn't been read yet.
    case looking
    /// The newest published export, or the one chosen, and its check.
    case export(TryOmarchyExportDisplay, TryOmarchyExportCheck)
    /// Omarchy holds a finished export that its shutdown publishes.
    case waiting(stopping: Bool)
    /// Nothing to bring across yet; Omarchy may be running or not.
    case none(running: Bool)

    public init(
      exports: TryOmarchyExportsDisplay, chosenID: String?, check: TryOmarchyExportCheck,
      exportWaitsInOutbox: Bool, phase: TrialPhase
    ) {
      let running: Bool
      switch phase {
      case .starting, .running: running = true
      case .stopping:
        self = exportWaitsInOutbox ? .waiting(stopping: true) : .looking
        return
      case .hidden, .ready, .failed: running = false
      }
      if exportWaitsInOutbox && running {
        self = .waiting(stopping: false)
        return
      }
      switch exports {
      case .notChecked:
        self = .looking
      case .found(let found, _):
        guard let newest = found.first else {
          self = .none(running: running)
          return
        }
        let chosen = chosenID.flatMap { id in found.first { $0.id == id } }
        self = .export(chosen ?? newest, chosen == nil ? .idle : check)
      case .unavailable:
        self = .none(running: running)
      }
    }

    public var title: String { PlainLanguage.trialMigrationTitle }

    public var message: String {
      switch self {
      case .looking: PlainLanguage.trialMigrationLooking
      case .export(let export, _): PlainLanguage.trialMigrationExport(export)
      case .waiting(let stopping):
        stopping ? PlainLanguage.trialMigrationPublishing : PlainLanguage.trialMigrationWaiting
      case .none(let running):
        running ? PlainLanguage.trialMigrationNoneRunning : PlainLanguage.trialMigrationNone
      }
    }

    /// The check's line under the message, for a published export.
    public var checkLine: String? {
      guard case .export(_, let check) = self else { return nil }
      switch check {
      case .idle, .checking: return PlainLanguage.tryExportChecking
      case .verified: return PlainLanguage.tryExportVerified
      case .failed(let message): return message
      }
    }

    /// Something is under way: reading Exports, checking the export, or
    /// shutting Omarchy down to publish it.
    public var isBusy: Bool {
      switch self {
      case .looking, .export(_, .idle), .export(_, .checking), .waiting(stopping: true): true
      case .export, .waiting, .none: false
      }
    }

    public var checkFailed: Bool {
      if case .export(_, .failed) = self { return true }
      return false
    }

    /// The default button, when the sheet has one besides Install Without
    /// Migrating.
    public var primary: TrialMigrationAction? {
      switch self {
      case .export(_, let check): .installWithExport(enabled: check == .verified)
      case .waiting(let stopping): .shutDownToPublish(enabled: !stopping)
      case .looking, .none: nil
      }
    }
  }

  public enum TrialMigrationAction: Equatable, Sendable {
    case installWithExport(enabled: Bool)
    case shutDownToPublish(enabled: Bool)

    public var title: String {
      switch self {
      case .installWithExport: PlainLanguage.trialInstallWithExport
      case .shutDownToPublish: PlainLanguage.trialShutDownToPublish
      }
    }

    public var isEnabled: Bool {
      switch self {
      case .installWithExport(let enabled), .shutDownToPublish(let enabled): enabled
      }
    }
  }
#endif

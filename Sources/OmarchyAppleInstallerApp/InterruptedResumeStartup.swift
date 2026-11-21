import OmarchyAppleInstallerTrustCore

enum InterruptedResumeStartup {
  /// A missing or older helper is never installed by startup inspection.
  /// Its matching protocol is required before reading protected records.
  @MainActor static func available(
    helperIsCurrent: () async -> Bool = { await InstallerHelperSetup.probeDisplay().isCurrent },
    discover: () async throws -> InterruptedInstallResumeReply = {
      try await InstallerHelperSetup.submitter().prepareInterruptedResume()
    }
  ) async -> Bool {
    guard await helperIsCurrent() else { return false }
    do {
      let reply = try await discover()
      return reply.automaticResumeAvailable && reply.ticket != nil
    } catch {
      return false
    }
  }
}

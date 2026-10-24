#if os(macOS)
  import Foundation
  import TryOmarchyKitClient
  import XCTest
  @testable import OmarchyInstallerUXCore

  @MainActor
  private final class FakeTrialEnvironment: TrialEnvironment {
    var isAvailable = true
    var currentSpace = TrialSpace(
      requiredBytes: 15_000_000_000, freeBytes: 40_000_000_000, hasVM: false)
    var permissionState = TrialPermissions(
      accessibility: .notAsked, microphone: .notAsked, camera: .refused)
    var startError: Error?
    var deleteError: Error?
    var shutdowns = 0
    var deletions = 0
    var onEvent: (@MainActor (VMRunEvent) -> Void)?
    var network: TrialBridgedNetwork = .notRegistered
    var networkChanges: [String] = []
    var registerError: Error?
    var unregisterError: Error?
    var runsFetchedKit = false

    func space() -> TrialSpace { currentSpace }
    func vmBytes() async -> Int64 { 9_400_000_000 }
    func permissions() -> TrialPermissions { permissionState }
    func requestAccessibility() { permissionState.accessibility = .allowed }
    func requestMicrophone() async { permissionState.microphone = .allowed }
    func requestCamera() async {}
    func start(onEvent: @escaping @MainActor (VMRunEvent) -> Void) throws {
      if let startError { throw startError }
      self.onEvent = onEvent
    }
    func shutDown() { shutdowns += 1 }
    func deleteVM() throws {
      if let deleteError { throw deleteError }
      deletions += 1
      currentSpace = TrialSpace(
        requiredBytes: 15_000_000_000, freeBytes: currentSpace.freeBytes, hasVM: false)
    }

    func bridgedNetwork() -> TrialBridgedNetwork { network }
    func registerBridgedNetwork() throws {
      networkChanges.append("register")
      if let registerError { throw registerError }
      network = .needsApproval
    }
    func openBridgedNetworkApproval() { networkChanges.append("open login items") }
    func unregisterBridgedNetwork() async throws {
      networkChanges.append("unregister")
      if let unregisterError { throw unregisterError }
      network = .notRegistered
    }

    /// A VM this app has started before, unless it comes from Try Omarchy.
    func makeVM() {
      currentSpace = TrialSpace(
        requiredBytes: 1_073_741_824, freeBytes: currentSpace.freeBytes, hasVM: true)
    }
  }

  private let tooLittleSpace = FailureDisplay(
    headline: "Free up at least 30 GB to install Omarchy",
    plainDetail: "Omarchy needs 60 GB.", remedy: "Remove files, then choose Check again.")

  @MainActor
  final class TrialSessionTests: XCTestCase {
    private func ready(_ environment: FakeTrialEnvironment) -> TrialSession {
      let trial = TrialSession(environment: environment)
      trial.present()
      return trial
    }

    func testTheTryScreenShowsSpaceAndOptionalPermissions() async {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      XCTAssertEqual(trial.landing.tryTitle, "Try it in a VM")
      guard case .ready(let space, let permissions) = trial.phase else {
        return XCTFail("Expected the Try screen")
      }
      XCTAssertTrue(space.isEnough)
      XCTAssertEqual(permissions.camera, .refused)
      XCTAssertTrue(trial.canStart)
      XCTAssertEqual(trial.startTitle, "Download and launch")
      trial.requestAccessibility()
      await trial.requestMicrophone()
      guard case .ready(_, let updated) = trial.phase else {
        return XCTFail("Expected the Try screen")
      }
      XCTAssertEqual(updated.accessibility, .allowed)
      XCTAssertEqual(updated.microphone, .allowed)
      trial.follow(.supported)
      trial.dismiss()
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertTrue(trial.showsLanding)
    }

    func testTooLittleSpaceKeepsStartDisabled() {
      let environment = FakeTrialEnvironment()
      environment.currentSpace = TrialSpace(
        requiredBytes: 15_000_000_000, freeBytes: 10_000_000_000, hasVM: false)
      let trial = ready(environment)
      XCTAssertFalse(trial.canStart)
      trial.start()
      XCTAssertNil(environment.onEvent)
      XCTAssertEqual(
        PlainLanguage.trialSpaceShortfall(environment.currentSpace),
        "Free up 5 GB more to try Omarchy.")
    }

    func testAnExistingVMStartsWithoutADownload() {
      let environment = FakeTrialEnvironment()
      environment.currentSpace = TrialSpace(
        requiredBytes: 1_073_741_824, freeBytes: 5_000_000_000, hasVM: true)
      XCTAssertEqual(ready(environment).startTitle, "Launch")
    }

    func testWithoutAKitThereIsNoTrial() {
      let environment = FakeTrialEnvironment()
      environment.isAvailable = false
      let trial = ready(environment)
      XCTAssertFalse(trial.isAvailable)
      XCTAssertEqual(trial.phase, .hidden)
    }

    func testRunsShutsDownAndContinues() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.progress(.downloading, percent: 42))
      XCTAssertEqual(trial.phase, .starting(TrialProgress(phase: .downloading, percent: 42)))
      XCTAssertTrue(trial.isActive)
      send(
        .notice(
          VMRunNotice(code: .bridgedNetworkUnavailable, message: "Starting with shared networking.")
        ))
      XCTAssertEqual(trial.phase, .starting(TrialProgress(phase: .downloading, percent: 42)))
      send(.ready)
      XCTAssertEqual(trial.phase, .running(environment.permissionState))

      var continued = 0
      trial.shutDown { continued += 1 }
      trial.shutDown { continued += 1 }
      XCTAssertEqual(trial.phase, .stopping)
      XCTAssertEqual(environment.shutdowns, 1)
      send(.stopping)
      send(.exited(status: 0))
      guard case .ready = trial.phase else { return XCTFail("The Try page stays, with Launch") }
      XCTAssertEqual(continued, 2)
      XCTAssertFalse(trial.isActive)

      var immediately = false
      trial.shutDown { immediately = true }
      XCTAssertTrue(immediately)
    }

    func testAnErrorNamesItsCause() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(
        .error(VMRunError(code: .vmAlreadyOpen, message: "run-qemu-gpu: this VM is already open")))
      send(.exited(status: 1))
      guard case .failed(let failure) = trial.phase else { return XCTFail("Expected a failure") }
      XCTAssertEqual(failure.code, .vmAlreadyOpen)
      XCTAssertEqual(failure.headline, "Omarchy is open in Try Omarchy")
      XCTAssertEqual(
        failure.detail,
        "Or in another copy of \(PlainLanguage.windowTitle). Shut it down there, then try again here."
      )
      XCTAssertEqual(failure.technicalDetail, "run-qemu-gpu: this VM is already open")
      trial.dismiss()
      guard case .ready = trial.phase else { return XCTFail("Back leads to the Try page") }
    }

    func testAnUnexpectedExitIsAFailure() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      send(.exited(status: 143))
      guard case .failed(let failure) = trial.phase else { return XCTFail("Expected a failure") }
      XCTAssertEqual(failure.code, .qemuExited)
    }

    func testAMacThatCannotRunTheVMFailsBeforeAnythingStarts() {
      let environment = FakeTrialEnvironment()
      environment.startError = VMRunError(code: .macOSTooOld, message: "needs macOS 15")
      let trial = ready(environment)
      trial.start()
      guard case .failed(let failure) = trial.phase else { return XCTFail("Expected a failure") }
      XCTAssertEqual(failure.code, .macOSTooOld)
    }

    // MARK: The landing page

    private func landing(_ status: TrialInstallStatus, vm: Bool = false) -> TrialSession {
      let environment = FakeTrialEnvironment()
      if vm { environment.makeVM() }
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.follow(status)
      if vm { trial.dismiss() }
      return trial
    }

    func testTheLandingPageOffersInstallAndTryInEveryInspectionState() {
      let notReady = FailureDisplay(
        headline: PlainLanguage.notReadyHeadline,
        plainDetail:
          "The disk compatibility check failed. Installation is unavailable. Details: engine")
      let statuses: [TrialInstallStatus] = [
        .checking, .supported,
        .unsupported(PlainLanguage.unsupportedModel(deviceIdentifier: "apple,j999")),
        .unsupported(tooLittleSpace), .unsupported(notReady), .installed,
        .unsupported(PlainLanguage.failure(for: URLError(.notConnectedToInternet))),
      ]
      for status in statuses {
        let trial = landing(status)
        XCTAssertEqual(trial.phase, .hidden, "\(status)")
        XCTAssertTrue(trial.showsLanding, "\(status)")
        XCTAssertEqual(trial.landing.installTitle, "Install on this Mac")
        XCTAssertEqual(trial.landing.tryTitle, "Try it in a VM")
        XCTAssertNil(trial.landing.tryLine)
      }
      XCTAssertEqual(
        landing(.supported).landing.choiceLine,
        "Install Omarchy on this Mac, or try it in a window beside macOS.")
      XCTAssertNil(landing(.checking).landing.choiceLine)
      XCTAssertNil(landing(.installed).landing.choiceLine)
      XCTAssertNil(landing(.supported, vm: true).landing.choiceLine)
    }

    func testTheLandingPageNamesTheSpaceForTry() {
      XCTAssertNil(landing(.supported).landing.spaceLine, "Enough room needs no line")
      XCTAssertEqual(
        landing(.supported, vm: true).landing.spaceLine,
        "40 GB is free on the disk that holds Omarchy’s virtual machine.")
      let environment = FakeTrialEnvironment()
      environment.currentSpace = TrialSpace(
        requiredBytes: 15_000_000_000, freeBytes: 10_000_000_000, hasVM: false)
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      XCTAssertEqual(trial.landing.spaceLine, "Try needs 15 GB of free space. 10 GB is free.")
      XCTAssertTrue(trial.landing.spaceIsShort)
      XCTAssertEqual(landing(.checking).landing.install, .checking)
      XCTAssertEqual(landing(.supported).landing.install, .available)
      XCTAssertFalse(landing(.supported).landing.tryIsPrimary)
      XCTAssertTrue(landing(.installed).landing.tryIsPrimary)
      XCTAssertEqual(PlainLanguage.trialCheckingInstall, "Checking this Mac…")
      XCTAssertFalse(landing(.finishingInstall).showsLanding)
      XCTAssertFalse(landing(.installerPage).showsLanding)
    }

    func testInstallIsDisabledWithItsReason() throws {
      func reason(_ status: TrialInstallStatus) throws -> TrialInstallReason {
        guard case .unavailable(let reason) = landing(status).landing.install else {
          XCTFail("Install is on offer for \(status)")
          throw CancellationError()
        }
        return reason
      }
      let model = try reason(
        .unsupported(
          PlainLanguage.unsupportedModel(
            deviceIdentifier: "apple,j999", supportedDeviceIdentifiers: ["apple,j314s"])))
      XCTAssertEqual(model.line, "Omarchy can’t be installed on this Mac model yet.")
      XCTAssertEqual(model.headline, PlainLanguage.blockedHeadline)
      XCTAssertTrue(model.detail.contains("This release supports these Macs"))
      XCTAssertTrue(model.canCheckAgain)

      let space = try reason(.unsupported(tooLittleSpace))
      XCTAssertEqual(space.line, "Free up at least 30 GB to install Omarchy")
      XCTAssertEqual(space.detail, "Omarchy needs 60 GB. Remove files, then choose Check again.")
      XCTAssertTrue(space.canCheckAgain)

      let notReady = try reason(
        .unsupported(
          FailureDisplay(
            headline: PlainLanguage.notReadyHeadline,
            plainDetail:
              "The disk compatibility check failed. Installation is unavailable. Details: engine")))
      XCTAssertEqual(notReady.line, "The disk compatibility check failed.")
      let externalDisk = try reason(
        .unsupported(
          FailureDisplay(
            headline: PlainLanguage.notReadyHeadline,
            plainDetail: "Start this installer from macOS on your Mac’s internal storage.")))
      XCTAssertEqual(
        externalDisk.line, "Start this installer from macOS on your Mac’s internal storage.")

      let offline = try reason(
        .unsupported(PlainLanguage.failure(for: URLError(.notConnectedToInternet))))
      XCTAssertEqual(offline.line, "The network request didn’t finish")
      XCTAssertTrue(offline.canCheckAgain)

      let installed = try reason(.installed)
      XCTAssertEqual(installed.line, "Omarchy is installed on this Mac.")
      XCTAssertEqual(installed.headline, "Omarchy is already installed")
      XCTAssertFalse(installed.canCheckAgain)

      let trial = landing(.unsupported(tooLittleSpace))
      XCTAssertEqual(trial.installMenuAction, .explain(space))
      var installs = 0
      trial.chooseInstall { installs += 1 }
      XCTAssertEqual(installs, 0)
      XCTAssertFalse(trial.showsMigrationNotice)
    }

    func testTryOpensTheTryPageAndBackReturns() {
      for status: TrialInstallStatus in [
        .checking, .supported, .installed, .unsupported(tooLittleSpace),
      ] {
        let trial = landing(status)
        trial.present()
        guard case .ready = trial.phase else { return XCTFail("Try opens the Try page") }
        XCTAssertFalse(trial.showsLanding)
        trial.dismiss()
        XCTAssertEqual(trial.phase, .hidden)
        XCTAssertTrue(trial.showsLanding)
      }
    }

    func testCheckAgainMovesOnlyTheInstallButton() {
      let trial = landing(.unsupported(tooLittleSpace))
      trial.follow(.checking)
      XCTAssertEqual(trial.landing.install, .checking)
      trial.follow(.supported)
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertEqual(trial.landing.install, .available)
      XCTAssertEqual(trial.installMenuAction, .install)
    }

    func testWithoutAVMInstallGoesStraightToThePlan() {
      let trial = landing(.supported)
      XCTAssertEqual(trial.installMenuTitle, "Install on this Mac…")
      var installs = 0
      trial.chooseInstall { installs += 1 }
      XCTAssertEqual(installs, 1)
      XCTAssertFalse(trial.showsMigrationNotice)
    }

    func testWithAVMTheLeftButtonIsMigrateToNative() {
      let trial = landing(.supported, vm: true)
      XCTAssertTrue(trial.showsLanding, "Back from the Try page leads here")
      XCTAssertEqual(trial.landing.installTitle, "Migrate to Native")
      XCTAssertEqual(trial.installTitle, "Migrate to Native")
      XCTAssertEqual(trial.installMenuTitle, "Migrate to Native…")
      XCTAssertEqual(trial.landing.install, .available)
      XCTAssertEqual(trial.landing.tryTitle, "Try it in a VM")
      XCTAssertEqual(trial.landing.tryLine, "Omarchy is set up and ready.")
      XCTAssertTrue(trial.landing.tryIsPrimary)

      var installs = 0
      trial.chooseInstall { installs += 1 }
      XCTAssertTrue(trial.showsMigrationNotice)
      XCTAssertEqual(installs, 0)
      XCTAssertEqual(
        PlainLanguage.trialMigrationNoticeTitle, "Moving your files to a native install is coming")
      XCTAssertEqual(PlainLanguage.trialInstallWithoutMigrating, "Install Without Migrating")
      trial.dismissMigrationNotice()
      trial.installWithoutMigrating()
      XCTAssertEqual(installs, 0, "Cancel leaves the page as it was")

      trial.chooseInstall { installs += 1 }
      trial.installWithoutMigrating()
      XCTAssertFalse(trial.showsMigrationNotice)
      XCTAssertEqual(installs, 1)
      XCTAssertEqual(trial.phase, .hidden)
    }

    func testMigrateToNativeIsDisabledLikeInstall() {
      let trial = landing(.unsupported(tooLittleSpace), vm: true)
      XCTAssertEqual(trial.landing.installTitle, "Migrate to Native")
      guard case .unavailable(let reason) = trial.landing.install else {
        return XCTFail("Migrate to Native waits for room, as Install does")
      }
      XCTAssertEqual(reason.line, "Free up at least 30 GB to install Omarchy")
      trial.chooseInstall {}
      XCTAssertFalse(trial.showsMigrationNotice)
      XCTAssertEqual(landing(.checking, vm: true).landing.install, .checking)
    }

    func testMigrateToNativeChosenWhileStartingKeepsTheWindowWhenOmarchyIsReady() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.follow(.supported)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)

      trial.chooseInstall {}
      send(.ready)
      XCTAssertTrue(trial.showsMigrationNotice)
      XCTAssertTrue(trial.showsWindow, "The confirmation stays until the person answers it")
    }

    func testInstallWithoutMigratingShutsOmarchyDownFirst() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      trial.follow(.supported)
      XCTAssertEqual(trial.installTitle, "Migrate to Native")

      var installs = 0
      trial.chooseInstall { installs += 1 }
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertEqual(environment.shutdowns, 0)
      trial.installWithoutMigrating()
      XCTAssertEqual(trial.phase, .stopping)
      XCTAssertEqual(environment.shutdowns, 1)
      XCTAssertEqual(installs, 0)
      send(.exited(status: 0))
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertEqual(installs, 1)
    }

    func testTheFirstStartTurnsInstallIntoMigrate() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      trial.follow(.supported)
      XCTAssertEqual(trial.installMenuTitle, "Install on this Mac…")
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      environment.makeVM()
      send(.ready)
      XCTAssertEqual(trial.installTitle, "Migrate to Native")
      XCTAssertEqual(trial.installMenuTitle, "Migrate to Native…")
    }

    func testTheTwoFreeSpaceNumbersAgree() throws {
      for free: UInt64 in [14_500_000_000, 15_400_000_000, 15_600_000_000, 464_200_000_000] {
        let chip = PlainLanguage.chipAndFreeSpace(chip: "Apple M1 Pro", freeBytes: free)
        let line = PlainLanguage.trialSpace(
          TrialSpace(requiredBytes: 1_073_741_824, freeBytes: Int64(free), hasVM: true))
        let number = PlainLanguage.bytes(free)
        XCTAssertEqual(chip, "Apple M1 Pro · \(number) free")
        XCTAssertEqual(line, "\(number) is free on the disk that holds Omarchy’s virtual machine.")
      }
      XCTAssertEqual(PlainLanguage.bytes(14_500_000_000), "15 GB")

      let folder = FileManager.default.temporaryDirectory.appendingPathComponent(
        "landing-space-\(UUID().uuidString)")
      try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
      defer { try? FileManager.default.removeItem(at: folder) }
      let tryReading = try XCTUnwrap(TrialSpace.of(folder).freeBytes)
      let headerReading = try XCTUnwrap(VolumeFreeSpace.bytes(at: folder))
      XCTAssertLessThan(abs(tryReading - headerReading), 100_000_000, "One reading for both")
    }

    func testTheInstallersOwnPageTakesTheFloor() {
      let environment = FakeTrialEnvironment()
      let trial = TrialSession(environment: environment)
      trial.follow(.installed)
      trial.present()
      trial.follow(.installerPage)
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertFalse(trial.showsLanding)
      XCTAssertEqual(trial.installMenuAction, .unavailable)
    }

    func testCheckAgainOnTheTryPageKeepsIt() {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.follow(.unsupported(tooLittleSpace))
      trial.present()
      trial.follow(.checking)
      trial.follow(.supported)
      guard case .ready = trial.phase else { return XCTFail("The Try page stays") }
      XCTAssertTrue(trial.canInstall)
    }

    /// Omarchy starts before the inspection finds an install waiting for its
    /// Recovery step; the steps take the page once Omarchy shuts down.
    func testRecoveryStepsWaitForOmarchyToShutDown() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      trial.follow(.finishingInstall)
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertTrue(trial.finishInstallAfterShutDown)
      XCTAssertEqual(trial.installMenuAction, .unavailable)
      trial.shutDown()
      send(.exited(status: 0))
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertFalse(trial.finishInstallAfterShutDown)
    }

    /// A failure while the Recovery steps wait must not strand the person on
    /// the Try page: Back leads to the steps.
    func testAFailureWhileRecoveryWaitsLeadsBackToTheSteps() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      trial.follow(.finishingInstall)
      send(.error(VMRunError(code: .qemuExited, message: "QEMU stopped")))
      send(.exited(status: 1))
      guard case .failed(let failure) = trial.phase else { return XCTFail("Expected a failure") }
      XCTAssertEqual(failure.code, .qemuExited)
      trial.dismiss()
      XCTAssertEqual(trial.phase, .hidden)
    }

    func testTheInstallersPageWaitsForOmarchyToShutDown() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      trial.follow(.installerPage)
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertFalse(trial.finishInstallAfterShutDown)
      trial.shutDown()
      send(.exited(status: 0))
      XCTAssertEqual(trial.phase, .hidden)
    }

    func testTheInstallerPhaseDecidesWhereTrySits() {
      let host = HostDisplay(chipAndSpace: "", supported: true)
      XCTAssertEqual(
        TrialInstallStatus(.inspecting, isBusy: true, pendingRecovery: false), .checking)
      XCTAssertEqual(
        TrialInstallStatus(.welcome(host), isBusy: false, pendingRecovery: false), .supported)
      XCTAssertEqual(
        TrialInstallStatus(.unsupported(tooLittleSpace), isBusy: false, pendingRecovery: false),
        .unsupported(tooLittleSpace))
      XCTAssertEqual(
        TrialInstallStatus(
          .existingInstallRefused(host: host), isBusy: true, pendingRecovery: false),
        .checking)
      XCTAssertEqual(
        TrialInstallStatus(
          .existingInstallRefused(host: host), isBusy: false, pendingRecovery: false),
        .installed)
      // An install waiting for its Recovery step keeps the installer's steps.
      XCTAssertEqual(
        TrialInstallStatus(
          .existingInstallRefused(host: host), isBusy: false, pendingRecovery: true),
        .finishingInstall)
      XCTAssertEqual(
        TrialInstallStatus(.failed(tooLittleSpace), isBusy: false, pendingRecovery: false),
        .installerPage)
    }

    #if DEBUG
      func testSimulatedMacsLandOnTheirPages() async {
        let expectations: [(InstallerSimulationScenario, (TrialInstallStatus) -> Bool)] = [
          (.success, { $0 == .supported }),
          (.existingInstall, { $0 == .installed }),
          (
            .unsupported,
            {
              if case .unsupported = $0 { return true }
              return false
            }
          ),
        ]
        for (scenario, expected) in expectations {
          let session = InstallerSession(
            environment: InstallerSimulationEnvironment(scenario: scenario, delay: .zero))
          await session.inspect()
          XCTAssertTrue(expected(session.trialInstallStatus), "\(scenario)")
        }
      }
    #endif

    // MARK: The remembered choice

    func testAVMOpensOnTheTryPageWithStart() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      XCTAssertTrue(trial.hasVM)
      trial.openAtLaunch()
      guard case .ready(let space, let permissions) = trial.phase else {
        return XCTFail("A VM opens on the Try page")
      }
      XCTAssertTrue(space.hasVM)
      XCTAssertEqual(permissions, environment.permissionState)
      XCTAssertNil(environment.onEvent, "Nothing starts before Launch")
      XCTAssertFalse(trial.isActive)
      XCTAssertEqual(trial.startTitle, "Launch")
      XCTAssertTrue(trial.canStart)
      XCTAssertEqual(trial.subheadline, PlainLanguage.trialReadySubheadline)

      // The inspection finishes in the background without moving the page.
      trial.follow(.checking)
      trial.follow(.supported)
      guard case .ready = trial.phase else { return XCTFail("The Try page stays") }
      trial.openAtLaunch()
      XCTAssertNil(environment.onEvent, "Once per launch")

      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertTrue(trial.canInstall)
      XCTAssertFalse(trial.canDeleteVM)
      send(.exited(status: 0))
      guard case .ready = trial.phase else { return XCTFail("A VM keeps the Try page") }

      trial.dismiss()
      XCTAssertTrue(trial.showsLanding)
      XCTAssertEqual(trial.landing.installTitle, "Migrate to Native")
    }

    func testEveryPermissionStillWaitsForStart() {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      environment.permissionState = TrialPermissions(
        accessibility: .allowed, microphone: .allowed, camera: .allowed)
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      guard case .ready = trial.phase else { return XCTFail("A VM opens on the Try page") }
      XCTAssertNil(environment.onEvent)
    }

    func testWithoutAVMTheLandingPageOpens() {
      let environment = FakeTrialEnvironment()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertTrue(trial.showsLanding)
      XCTAssertNil(environment.onEvent)
    }

    func testAStartThatFailsBeforeTheInspectionKeepsTheVM() async {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      environment.startError = VMRunError(code: .launchFailed, message: "kit missing")
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      guard case .failed(let failure) = trial.phase else { return XCTFail("Expected a failure") }
      XCTAssertEqual(failure.code, .launchFailed)
      await trial.settled()
      XCTAssertEqual(trial.vmBytes, 9_400_000_000, "The plan review still sees the VM")
      XCTAssertTrue(trial.canDeleteVM)

      trial.follow(.checking)
      trial.follow(.supported)
      guard case .failed = trial.phase else { return XCTFail("The failure stays on the page") }
      trial.dismiss()
      guard case .ready = trial.phase else { return XCTFail("Back leads to the Try page") }
      XCTAssertEqual(trial.startTitle, "Launch")
    }

    // MARK: Coming from Try Omarchy

    func testAVMFromTryOmarchyOpensOnTheTryPage() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      guard case .ready(let space, let permissions) = trial.phase else {
        return XCTFail("Expected the Try page")
      }
      XCTAssertTrue(space.hasVM)
      XCTAssertFalse(permissions.allAllowed)
      XCTAssertTrue(trial.subheadline.hasPrefix("Omarchy is set up and ready."))
      trial.dismiss()
      XCTAssertTrue(trial.showsLanding)
      XCTAssertEqual(trial.landing.installTitle, "Migrate to Native")
      XCTAssertEqual(trial.landing.tryLine, "Omarchy is set up and ready.")
    }

    func testTheSpaceLineNamesTheVMsDisk() {
      XCTAssertEqual(
        PlainLanguage.trialSpace(
          TrialSpace(requiredBytes: 1_073_741_824, freeBytes: 400_000_000_000, hasVM: true)),
        "400 GB is free on the disk that holds Omarchy’s virtual machine.")
    }

    func testOmarchyOpenInTryOmarchyOffersTryAgain() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.error(VMRunError(code: .vmAlreadyOpen, message: "this VM is already open")))
      send(.exited(status: 1))
      guard case .failed(let failure) = trial.phase else { return XCTFail("Expected a failure") }
      XCTAssertEqual(failure.headline, "Omarchy is open in Try Omarchy")
      XCTAssertTrue(trial.canTryAgain)

      environment.onEvent = nil
      trial.tryAgain()
      XCTAssertTrue(trial.isActive)
      XCTAssertNotNil(environment.onEvent)

      environment.startError = VMRunError(code: .macOSTooOld, message: "needs macOS 15")
      trial.shutDown()
      environment.onEvent?(.exited(status: 0))
      trial.start()
      guard case .failed = trial.phase else { return XCTFail("Expected a failure") }
      XCTAssertFalse(trial.canTryAgain)
    }

    func testInstallContinuesWhenOmarchyStopsWithAFailure() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.follow(.supported)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      var installs = 0
      trial.leaveForInstall { installs += 1 }
      send(.error(VMRunError(code: .qemuExited, message: "QEMU stopped")))
      send(.exited(status: 1))
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertEqual(installs, 1)
    }

    func testInstallOnThisMacShutsOmarchyDownFirst() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)

      var installs = 0
      trial.leaveForInstall { installs += 1 }
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertEqual(installs, 0, "Nothing happens before the Mac is known to be supported")

      trial.follow(.supported)
      trial.leaveForInstall { installs += 1 }
      XCTAssertEqual(trial.phase, .stopping)
      XCTAssertEqual(environment.shutdowns, 1)
      XCTAssertEqual(installs, 0)
      send(.exited(status: 0))
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertEqual(installs, 1)

      // From the Try page, Install leaves at once.
      trial.present()
      trial.leaveForInstall { installs += 1 }
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertEqual(installs, 2)
    }

    // MARK: The running page

    func testTheRunningPageListsWhatIsStillNotAllowed() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      guard case .running(let before) = trial.phase else { return XCTFail("Expected running") }
      XCTAssertFalse(before.allAllowed)
      XCTAssertEqual(before.accessibility, .notAsked)
      trial.requestAccessibility()
      guard case .running(let after) = trial.phase else { return XCTFail("Expected running") }
      XCTAssertEqual(after.accessibility, .allowed)
      XCTAssertEqual(
        PlainLanguage.trialPermissionsNextStart,
        "A permission you allow now takes effect the next time Omarchy starts.")
    }

    // MARK: Delete Virtual Machine

    func testDeletingTheVMReturnsToTheFirstLaunch() async throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.follow(.supported)
      trial.openAtLaunch()
      XCTAssertTrue(trial.canDeleteVM)
      trial.start()
      XCTAssertFalse(trial.canDeleteVM)
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      trial.shutDown()
      send(.exited(status: 0))
      XCTAssertTrue(trial.canDeleteVM)

      let message = await trial.deletionMessage()
      XCTAssertEqual(
        message,
        "Omarchy’s virtual machine, with everything saved in it, moves to the Trash. Emptying the Trash frees up to 9 GB. The next start downloads Omarchy again."
      )
      try trial.deleteVM()
      XCTAssertEqual(environment.deletions, 1)
      XCTAssertEqual(trial.phase, .hidden)
      XCTAssertFalse(trial.hasVM)
      XCTAssertFalse(trial.canDeleteVM)
      XCTAssertEqual(trial.vmBytes, 0)
    }

    func testDeletingOnAnUnsupportedMacReturnsToTheLandingPage() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.follow(.unsupported(tooLittleSpace))
      trial.present()
      try trial.deleteVM()
      XCTAssertTrue(trial.showsLanding)
      XCTAssertEqual(trial.landing.installTitle, "Install on this Mac")
      trial.present()
      XCTAssertEqual(trial.startTitle, "Download and launch")
    }

    func testAFailedDeletionKeepsTheVM() {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      environment.deleteError = TrialVMFolder.Refusal.open
      let trial = TrialSession(environment: environment)
      trial.follow(.supported)
      XCTAssertThrowsError(try trial.deleteVM())
      XCTAssertTrue(trial.hasVM)
      XCTAssertEqual(
        TrialVMFolder.Refusal.open.localizedDescription,
        "Omarchy is open in Try Omarchy or another copy of this app. Shut it down there first.")
    }

    private func runningWithSharedNetworking(_ environment: FakeTrialEnvironment) throws -> (
      TrialSession, @MainActor (VMRunEvent) -> Void
    ) {
      let trial = ready(environment)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      XCTAssertNil(trial.bridgedNetworkLine)
      send(.notice(VMRunNotice(code: .bridgedNetworkUnavailable, message: "not registered")))
      send(.progress(.checking, percent: nil))
      send(.ready)
      return (trial, send)
    }

    func testASharedNetworkingNoticeOffersToSetUpBridgedNetworking() async throws {
      let environment = FakeTrialEnvironment()
      let (trial, send) = try runningWithSharedNetworking(environment)
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertEqual(
        trial.bridgedNetworkLine, PlainLanguage.trialBridgedNetworkLine(.notRegistered))
      XCTAssertEqual(trial.bridgedNetworkActionTitle, "Set Up Bridged Networking")
      XCTAssertFalse(trial.canChangeBridgedNetwork)

      await trial.setUpBridgedNetwork()
      XCTAssertEqual(environment.networkChanges, ["register", "open login items"])
      XCTAssertEqual(trial.bridgedNetwork, .needsApproval)
      XCTAssertEqual(trial.bridgedNetworkActionTitle, "Open Login Items")
      XCTAssertEqual(environment.shutdowns, 0)
      await trial.setUpBridgedNetwork()
      XCTAssertEqual(
        environment.networkChanges, ["register", "open login items", "open login items"])

      send(.exited(status: 0))
      XCTAssertNil(trial.bridgedNetworkLine)
      trial.present()
      trial.start()
      XCTAssertNotNil(environment.onEvent)
      XCTAssertFalse(trial.bridgedNetworkUnavailable)
    }

    func testAFailedRegistrationSaysWhy() async throws {
      let environment = FakeTrialEnvironment()
      environment.registerError = VMRunError(
        code: .launchFailed, message: "Operation not permitted")
      let (trial, _) = try runningWithSharedNetworking(environment)
      await trial.setUpBridgedNetwork()
      XCTAssertEqual(
        trial.bridgedNetworkLine,
        "Bridged networking couldn’t be changed: Operation not permitted")
      XCTAssertEqual(trial.bridgedNetwork, .notRegistered)
    }

    func testRepairAndRemoveShutOmarchyDownFirst() async throws {
      let environment = FakeTrialEnvironment()
      environment.network = .enabled
      let (trial, send) = try runningWithSharedNetworking(environment)
      XCTAssertEqual(trial.bridgedNetworkActionTitle, "Repair")
      XCTAssertTrue(trial.canChangeBridgedNetwork)
      let repair = Task { await trial.repairBridgedNetwork() }
      await Task.yield()
      XCTAssertEqual(trial.phase, .stopping)
      XCTAssertEqual(environment.shutdowns, 1)
      XCTAssertEqual(environment.networkChanges, [])
      XCTAssertTrue(trial.bridgedNetworkChanging)
      XCTAssertFalse(trial.canChangeBridgedNetwork)
      XCTAssertFalse(trial.canRepairBridgedNetwork)
      XCTAssertNil(trial.bridgedNetworkActionTitle)
      await trial.repairBridgedNetwork()
      await trial.removeBridgedNetwork()
      await trial.setUpBridgedNetwork()
      XCTAssertEqual(environment.shutdowns, 1)
      send(.exited(status: 0))
      await repair.value
      XCTAssertFalse(trial.bridgedNetworkChanging)
      XCTAssertEqual(environment.networkChanges, ["unregister", "register", "open login items"])

      environment.network = .enabled
      trial.refresh()
      await trial.removeBridgedNetwork()
      XCTAssertEqual(environment.networkChanges.last, "unregister")
      XCTAssertEqual(trial.bridgedNetwork, .notRegistered)
      XCTAssertFalse(trial.canChangeBridgedNetwork)
    }

    func testAFailedRemoveSaysWhyOnceOmarchyIsDown() async throws {
      let environment = FakeTrialEnvironment()
      environment.network = .enabled
      environment.unregisterError = CocoaError(.fileWriteNoPermission)
      let (trial, send) = try runningWithSharedNetworking(environment)
      let remove = Task { await trial.removeBridgedNetwork() }
      await Task.yield()
      send(.exited(status: 0))
      let failure = await remove.value
      XCTAssertEqual(failure, CocoaError(.fileWriteNoPermission).localizedDescription)
      XCTAssertFalse(trial.isActive)
      XCTAssertNil(trial.bridgedNetworkLine, "The running page's line is gone with Omarchy")
    }

    func testAFetchedKitsSharedNetworkingOffersNoRepair() async throws {
      let environment = FakeTrialEnvironment()
      environment.network = .enabled
      environment.runsFetchedKit = true
      let (trial, send) = try runningWithSharedNetworking(environment)
      XCTAssertEqual(trial.bridgedNetworkLine, PlainLanguage.trialBridgedNetworkFetchedKitLine)
      XCTAssertNil(trial.bridgedNetworkActionTitle)
      XCTAssertFalse(trial.canRepairBridgedNetwork)
      XCTAssertTrue(trial.canChangeBridgedNetwork)
      await trial.setUpBridgedNetwork()
      await trial.repairBridgedNetwork()
      XCTAssertEqual(environment.shutdowns, 0)
      XCTAssertEqual(environment.networkChanges, [])
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      send(.exited(status: 0))
      XCTAssertTrue(trial.canRepairBridgedNetwork)
    }

    func testTheChangeConfirmationNamesTheShutdown() throws {
      let environment = FakeTrialEnvironment()
      environment.network = .enabled
      let (trial, send) = try runningWithSharedNetworking(environment)
      XCTAssertTrue(
        trial.bridgedNetworkChangeMessage(removing: true).hasPrefix("Omarchy shuts down first."))
      XCTAssertTrue(
        trial.bridgedNetworkChangeMessage(removing: false).hasPrefix("Omarchy shuts down first."))
      send(.exited(status: 0))
      XCTAssertFalse(trial.bridgedNetworkChangeMessage(removing: true).contains("shuts down"))
    }

    func testEveryCodeAndPhaseHasWords() {
      for code in VMRunErrorCode.allCases {
        let words = PlainLanguage.trialFailure(code)
        XCTAssertFalse(words.headline.isEmpty)
        XCTAssertFalse(words.detail.isEmpty)
      }
      for phase in VMRunPhase.allCases {
        XCTAssertFalse(PlainLanguage.trialProgressTitle(phase).isEmpty)
      }
      XCTAssertNil(PlainLanguage.trialDiskNotice(bytes: 0))
      XCTAssertEqual(
        PlainLanguage.trialDiskNotice(bytes: 9_400_000_000),
        "Omarchy’s virtual machine uses up to 9 GB of macOS’s space, already counted above. Installing keeps it, in Application Support › Try Omarchy."
      )
    }

    // MARK: The installer's window while Omarchy runs

    func testTheWindowClosesWhileOmarchyRunsAndReturnsWhenItShutsDown() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      XCTAssertTrue(trial.showsWindow)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.progress(.downloading, percent: 10))
      XCTAssertTrue(trial.showsWindow, "The download shows on the page")
      send(.ready)
      XCTAssertFalse(trial.showsWindow, "Omarchy's own window takes over")
      XCTAssertTrue(trial.canShutDown)

      trial.shutDown()
      XCTAssertTrue(trial.showsWindow, "Shutting down shows on the page")
      XCTAssertFalse(trial.canShutDown)
      send(.exited(status: 0))
      XCTAssertTrue(trial.showsWindow)
      guard case .ready = trial.phase else { return XCTFail("The Try page returns, with Launch") }
    }

    func testTheWindowReturnsWhenOmarchyShutsItselfDownOrFails() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      trial.start()
      var send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      send(.stopping)
      XCTAssertTrue(trial.showsWindow)
      XCTAssertEqual(trial.phase, .stopping)
      send(.exited(status: 0))
      XCTAssertTrue(trial.showsWindow)

      trial.start()
      send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      XCTAssertFalse(trial.showsWindow)
      send(.error(VMRunError(code: .qemuExited, message: "QEMU stopped")))
      send(.exited(status: 1))
      XCTAssertTrue(trial.showsWindow)
      guard case .failed = trial.phase else { return XCTFail("The failure shows on the page") }
    }

    func testTheWindowStaysWhenShutDownComesBeforeOmarchyIsUp() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      trial.shutDown()
      send(.ready)
      XCTAssertTrue(trial.showsWindow)
      XCTAssertEqual(trial.phase, .stopping)
    }

    func testReopeningShowsTheRunningPageReadAgain() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      XCTAssertFalse(trial.showsWindow, "The remembered start closes the window too")

      environment.permissionState.microphone = .allowed
      trial.reopenWindow()
      XCTAssertTrue(trial.showsWindow)
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertTrue(trial.isActive)
    }

    func testMigrateFromTheMenuBringsTheWindowBackForItsNotice() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      trial.follow(.supported)
      trial.chooseInstall {}
      XCTAssertTrue(trial.showsMigrationNotice)
      XCTAssertTrue(trial.showsWindow)
    }

    /// A grant made in System Settings shows once the app is active again or
    /// System Settings closes, both of which refresh the page.
    func testAGrantInSystemSettingsShowsOnRefresh() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      environment.permissionState.accessibility = .allowed
      guard case .ready(_, let before) = trial.phase else { return XCTFail("The Try page") }
      XCTAssertEqual(before.accessibility, .notAsked)
      trial.refresh()
      guard case .ready(_, let after) = trial.phase else { return XCTFail("The Try page") }
      XCTAssertEqual(after.accessibility, .allowed)

      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      environment.permissionState.camera = .allowed
      trial.refresh()
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertFalse(trial.showsWindow, "A refresh leaves the window where it is")
    }

    /// Ordering the last window out asks the app whether to quit. Quitting
    /// shuts Omarchy down, so while Omarchy runs the answer must be no.
    func testClosingTheWindowOnReadyNeverShutsOmarchyDown() throws {
      let environment = FakeTrialEnvironment()
      environment.makeVM()
      let trial = TrialSession(environment: environment)
      XCTAssertTrue(trial.quitsWhenTheLastWindowCloses)
      trial.openAtLaunch()
      trial.start()
      let send = try XCTUnwrap(environment.onEvent)
      XCTAssertFalse(trial.quitsWhenTheLastWindowCloses, "Starting keeps the app open")
      send(.ready)
      XCTAssertFalse(trial.showsWindow)
      XCTAssertFalse(trial.quitsWhenTheLastWindowCloses)
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertEqual(environment.shutdowns, 0)
      trial.refresh()
      XCTAssertEqual(trial.phase, .running(environment.permissionState))
      XCTAssertEqual(environment.shutdowns, 0)

      send(.stopping)
      send(.exited(status: 0))
      XCTAssertTrue(trial.quitsWhenTheLastWindowCloses)
    }

    func testOmarchysWindowIsTheOneDockTileWhileItRuns() throws {
      let environment = FakeTrialEnvironment()
      let trial = ready(environment)
      XCTAssertTrue(trial.showsInDock)
      trial.start()
      var send = try XCTUnwrap(environment.onEvent)
      XCTAssertTrue(trial.showsInDock, "The download shows in the installer")
      send(.ready)
      XCTAssertFalse(trial.showsInDock)
      send(.exited(status: 0))
      XCTAssertTrue(trial.showsInDock)

      trial.start()
      send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      XCTAssertFalse(trial.showsInDock)
      send(.error(VMRunError(code: .qemuExited, message: "QEMU stopped")))
      send(.exited(status: 1))
      XCTAssertTrue(trial.showsInDock)

      trial.dismiss()
      trial.start()
      send = try XCTUnwrap(environment.onEvent)
      send(.ready)
      trial.shutDown()
      XCTAssertTrue(trial.showsInDock, "Shutting down shows in the installer")
    }
  }
#endif

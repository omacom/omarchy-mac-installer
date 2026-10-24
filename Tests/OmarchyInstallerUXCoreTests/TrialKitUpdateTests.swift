#if os(macOS)
  import Foundation
  import OmarchyAppleInstallerTrustCore
  import TryOmarchyKitClient
  import XCTest
  @testable import OmarchyInstallerUXCore

  private final class Clock: @unchecked Sendable {
    private let lock = NSLock()
    private var date = Date(timeIntervalSince1970: 1_800_000_000)
    var now: Date { lock.withLock { date } }
    func advance(hours: Double) { lock.withLock { date += hours * 3_600 } }
  }

  final class TrialKitUpdateTests: XCTestCase {
    private var base: URL!
    private var embedded: TrialKitLayout!
    private var store: TrialKitStore!
    private var releases: FakeTrialKitReleases!
    private var clock: Clock!
    private var evaluator = FakeCodeEvaluator()
    private var network = FakeNetwork()
    private var policy = TrialKitSignaturePolicy.release

    override func setUpWithError() throws {
      base = FileManager.default.temporaryDirectory
        .appendingPathComponent("trial-kit-\(UUID().uuidString)", isDirectory: true)
      try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
      embedded = try TrialKitFixture.make(
        at: base.appendingPathComponent("App/Contents/Helpers"), version: "v1.2.0"
      ).layout
      store = TrialKitStore.under(
        applicationSupport: base.appendingPathComponent("Application Support"),
        workspace: InstallerProductIdentity.appIdentifier)
      releases = FakeTrialKitReleases()
      clock = Clock()
    }

    override func tearDownWithError() throws {
      try? FileManager.default.removeItem(at: base)
    }

    private func updater(downloads: FakeCodeEvaluator? = nil) -> TrialKitUpdater {
      let clock = clock!
      return TrialKitUpdater(
        store: store, embedded: embedded, source: releases,
        signatures: TrialKitSignatureCheck(policy: policy, evaluator: downloads ?? evaluator),
        startSignatures: TrialKitSignatureCheck(policy: policy, evaluator: evaluator),
        network: network, now: { clock.now }, networkWait: .milliseconds(50))
    }

    private func publish(_ version: String, protocol runProtocol: Int = 1) throws {
      try releases.publish(version, protocol: runProtocol, under: base)
    }

    private func fetchedKits() throws -> [String] {
      try FileManager.default.contentsOfDirectory(atPath: store.directory.path)
        .filter { $0.hasPrefix("kit-") }.sorted()
    }

    private func downloads() throws -> [String] {
      try FileManager.default.contentsOfDirectory(atPath: store.directory.path)
        .filter { $0.hasPrefix("incoming") }
    }

    private func version(_ text: String) -> TryOmarchyKitVersion { TryOmarchyKitVersion(text)! }

    // MARK: Checking and switching

    func testANewerReleaseIsUsedFromTheNextStartAndTheEmbeddedKitIsUntouched() async throws {
      try publish("v1.3.0")
      let kits = updater()
      XCTAssertEqual(kits.kitForNextStart().layout, embedded)
      let before = try Data(contentsOf: embedded.manifestURL)
      let outcome = await kits.checkIfDue()
      XCTAssertEqual(outcome, .installed(version("v1.3.0")))
      let next = kits.kitForNextStart().layout
      XCTAssertNotEqual(next, embedded)
      XCTAssertEqual(
        next.root.deletingLastPathComponent().standardizedFileURL,
        store.directory.standardizedFileURL)
      XCTAssertEqual(try next.manifest().version, version("v1.3.0"))
      XCTAssertTrue(next.hasLauncher)
      XCTAssertEqual(try Data(contentsOf: embedded.manifestURL), before)
      XCTAssertEqual(try downloads(), [])
      let pointer = try FileManager.default.destinationOfSymbolicLink(
        atPath: store.directory.appendingPathComponent("current").path)
      XCTAssertEqual(pointer, next.root.lastPathComponent)
    }

    func testTheReleasesAreAskedNoMoreThanOnceADay() async throws {
      try publish("v1.3.0")
      let kits = updater()
      let first = await kits.checkIfDue()
      XCTAssertEqual(first, .installed(version("v1.3.0")))
      clock.advance(hours: 23)
      let second = await kits.checkIfDue()
      XCTAssertEqual(second, .notDue)
      XCTAssertEqual(releases.fetched.filter { $0 == .latestManifest }.count, 1)
      clock.advance(hours: 2)
      let third = await kits.checkIfDue()
      XCTAssertEqual(third, .upToDate(version("v1.3.0")))
      XCTAssertEqual(releases.fetched.filter { $0 == .latestManifest }.count, 2)
    }

    func testAClockThatWentBackChecksAgain() async throws {
      try publish("v1.2.0")
      let kits = updater()
      _ = await kits.checkIfDue()
      clock.advance(hours: -48)
      let outcome = await kits.checkIfDue()
      XCTAssertEqual(outcome, .upToDate(version("v1.2.0")))
      XCTAssertEqual(releases.fetched.filter { $0 == .latestManifest }.count, 2)
    }

    func testAnOlderOrEqualReleaseIsNotDownloaded() async throws {
      for older in ["v1.1.9", "v1.2.0"] {
        try publish(older)
        clock.advance(hours: 25)
        let outcome = await updater().checkIfDue()
        XCTAssertEqual(outcome, .upToDate(version("v1.2.0")))
      }
      XCTAssertEqual(Set(releases.fetched), [.latestManifest])
      XCTAssertNil(store.current())
    }

    func testANewKitMustBeNewerThanTheFetchedOneTooAndOnlyTheNewestIsKept() async throws {
      try publish("v1.3.0")
      let kits = updater()
      _ = await kits.checkIfDue()
      var running: TrialKitChoice? = kits.kitForNextStart()
      XCTAssertEqual(running?.isFetched, true)
      let first = try fetchedKits()
      XCTAssertEqual(first.count, 1)

      clock.advance(hours: 25)
      let same = await kits.checkIfDue()
      XCTAssertEqual(same, .upToDate(version("v1.3.0")))

      try publish("v1.4.0")
      clock.advance(hours: 25)
      let newer = await kits.checkIfDue()
      XCTAssertEqual(newer, .installed(version("v1.4.0")))
      XCTAssertEqual(try fetchedKits().count, 2, "the running kit stays while it runs")
      let next = updater().kitForNextStart()
      XCTAssertEqual(try next.layout.manifest().version, version("v1.4.0"))
      XCTAssertTrue(try fetchedKits().contains(first[0]))
      running = nil
      XCTAssertNil(running)
      XCTAssertEqual(try kits.kitForNextStart().layout.manifest().version, version("v1.4.0"))
      XCTAssertEqual(try fetchedKits().count, 1)
      XCTAssertFalse(try fetchedKits().contains(first[0]))
    }

    func testTwoInstallersSharingTheFolderKeepEachOthersKitsAndDownloads() async throws {
      try publish("v1.3.0")
      let first = updater()
      _ = await first.checkIfDue()
      let running = first.kitForNextStart()
      XCTAssertTrue(running.isFetched)
      try publish("v1.4.0")
      clock.advance(hours: 25)
      let second = updater()
      let outcome = await second.checkIfDue()
      XCTAssertEqual(outcome, .installed(version("v1.4.0")))
      let (download, lease) = try store.freshIncoming()
      _ = second.kitForNextStart()
      XCTAssertTrue(FileManager.default.fileExists(atPath: running.layout.root.path))
      XCTAssertTrue(FileManager.default.fileExists(atPath: download.path))
      withExtendedLifetime(lease) {}
      withExtendedLifetime(running) {}
    }

    func testAnOlderKitNeverReplacesANewerOneAnotherInstallerSwitchedTo() async throws {
      try publish("v1.4.0")
      _ = await updater().checkIfDue()
      let older = base.appendingPathComponent("older")
      try TrialKitFixture.make(at: older, version: "v1.3.0")
      let kept = try store.adopt(older, version: version("v1.3.0"))
      XCTAssertEqual(try kept.manifest().version, version("v1.4.0"))
      XCTAssertFalse(FileManager.default.fileExists(atPath: older.path))
    }

    func testADevelopmentReleaseIsRefused() async throws {
      releases.serve(TrialKitFixture.manifest("development"), as: .latestManifest)
      let outcome = await updater().checkIfDue()
      XCTAssertEqual(outcome, .failed("developmentRelease"))
      XCTAssertNil(store.current())
    }

    func testADevelopmentEmbeddedKitIsNeverReplaced() async throws {
      try FileManager.default.removeItem(at: embedded.root)
      embedded = try TrialKitFixture.make(at: embedded.root, version: "development").layout
      try publish("v9.0.0")
      let outcome = await updater().checkIfDue()
      XCTAssertEqual(outcome, .upToDate(.development))
      XCTAssertTrue(releases.fetched.isEmpty)
    }

    func testAnEmbeddedKitWithoutAManifestIsOlderThanAnyRelease() async throws {
      try FileManager.default.removeItem(at: embedded.manifestURL)
      try publish("v0.1.0")
      let kits = updater()
      let outcome = await kits.checkIfDue()
      XCTAssertEqual(outcome, .installed(version("v0.1.0")))
      XCTAssertEqual(try kits.kitForNextStart().layout.manifest().version, version("v0.1.0"))
    }

    func testAKitForANewerRunProtocolIsSkippedAndTheCurrentOneKept() async throws {
      try publish("v1.3.0")
      let kits = updater()
      _ = await kits.checkIfDue()
      let current = kits.kitForNextStart().layout
      try publish("v2.0.0", protocol: VMRunProtocol.version + 1)
      clock.advance(hours: 25)
      let outcome = await kits.checkIfDue()
      XCTAssertEqual(outcome, .needsNewerInstaller(version("v2.0.0")))
      XCTAssertFalse(releases.fetched.contains(.archive(version("v2.0.0"))))
      XCTAssertEqual(kits.kitForNextStart().layout, current)
    }

    func testCellularOrLowDataModeWaitsForTheNextStart() async throws {
      network.snapshot = InstallerNetworkPathSnapshot(
        isSatisfied: true, isExpensive: true, isConstrained: false)
      try publish("v1.3.0")
      let metered = await updater().checkIfDue()
      XCTAssertEqual(metered, .meteredNetwork)
      XCTAssertTrue(releases.fetched.isEmpty)
      XCTAssertNil(store.lastCheck())
      network.snapshot = InstallerNetworkPathSnapshot(
        isSatisfied: true, isExpensive: false, isConstrained: true)
      let constrained = await updater().checkIfDue()
      XCTAssertEqual(constrained, .meteredNetwork)
      network = FakeNetwork()
      let open = await updater().checkIfDue()
      XCTAssertEqual(open, .installed(version("v1.3.0")))
    }

    // MARK: Failures leave the current kit

    private func installFirstKit() async throws -> (TrialKitUpdater, TrialKitLayout) {
      try publish("v1.3.0")
      let kits = updater()
      _ = await kits.checkIfDue()
      clock.advance(hours: 25)
      return (kits, kits.kitForNextStart().layout)
    }

    func testAFetchedKitIsCheckedAgainAtEveryStart() async throws {
      let (_, current) = try await installFirstKit()
      evaluator.fallback.team = "AAAAAAAAAA"
      let choice = updater().kitForNextStart()
      XCTAssertEqual(choice.layout, embedded)
      XCTAssertFalse(choice.isFetched)
      XCTAssertEqual(choice.refusal?.hasPrefix("signature: "), true)
      XCTAssertFalse(FileManager.default.fileExists(atPath: current.root.path))
      evaluator.fallback = FakeCodeEvaluator.Code()
      let outcome = await updater().checkIfDue()
      XCTAssertEqual(outcome, .installed(version("v1.3.0")))
      XCTAssertEqual(try updater().kitForNextStart().layout.manifest().version, version("v1.3.0"))
    }

    func testAKitAnotherStartRunsStaysWhenItFailsACheck() async throws {
      let (kits, current) = try await installFirstKit()
      let running = kits.kitForNextStart()
      XCTAssertTrue(running.isFetched)
      evaluator.fallback = FakeCodeEvaluator.Code(valid: false)
      XCTAssertEqual(updater().kitForNextStart().layout, embedded)
      XCTAssertTrue(FileManager.default.fileExists(atPath: current.root.path))
      withExtendedLifetime(running) {}
    }

    func testAKitTamperedWithAfterItsDownloadIsNotRun() async throws {
      let (_, current) = try await installFirstKit()
      try TrialKitFixture.machO.write(to: current.root.appendingPathComponent("planted.dylib"))
      evaluator.code["planted.dylib"] = FakeCodeEvaluator.Code(team: nil, runtime: false)
      XCTAssertEqual(updater().kitForNextStart().layout, embedded)
    }

    private func assertFails(
      _ kits: TrialKitUpdater, keeping current: TrialKitLayout, file: StaticString = #filePath,
      line: UInt = #line
    ) async throws {
      let outcome = await kits.checkIfDue()
      guard case .failed = outcome else {
        return XCTFail("Expected a failure, got \(outcome)", file: file, line: line)
      }
      XCTAssertEqual(kits.kitForNextStart().layout, current, file: file, line: line)
      XCTAssertEqual(try fetchedKits(), [current.root.lastPathComponent], file: file, line: line)
      XCTAssertEqual(try downloads(), [], file: file, line: line)
    }

    func testAFailedDownloadLeavesTheCurrentKit() async throws {
      let (kits, current) = try await installFirstKit()
      try publish("v1.4.0")
      releases.fail(.archive(version("v1.4.0")), with: TrialKitDownloadError.failed("lost"))
      try await assertFails(kits, keeping: current)
    }

    func testAMissingReleaseLeavesTheEmbeddedKit() async throws {
      let kits = updater()
      let outcome = await kits.checkIfDue()
      XCTAssertEqual(outcome, .failed("download: httpStatus(404)"))
      XCTAssertEqual(kits.kitForNextStart().layout, embedded)
    }

    func testADigestMismatchIsRefused() async throws {
      let (kits, current) = try await installFirstKit()
      try publish("v1.4.0")
      releases.serve(
        Data("\(String(repeating: "0", count: 64))  TryOmarchyKit.zip\n".utf8),
        as: .checksum(version("v1.4.0")))
      try await assertFails(kits, keeping: current)
    }

    func testAMalformedChecksumFileIsRefused() throws {
      let digest = String(repeating: "a", count: 64)
      XCTAssertEqual(
        try TrialKitUpdater.digest(inChecksumFile: Data("\(digest)  TryOmarchyKit.zip\n".utf8)),
        digest)
      for text in [
        "\(digest) TryOmarchyKit.zip\n", "\(digest)  Other.zip\n",
        "\(digest.uppercased())  TryOmarchyKit.zip\n",
        "\(digest)  TryOmarchyKit.zip\n\(digest)  TryOmarchyKit.zip\n", "abc  TryOmarchyKit.zip\n",
        "",
      ] {
        XCTAssertThrowsError(try TrialKitUpdater.digest(inChecksumFile: Data(text.utf8)), text)
      }
    }

    func testAnArchiveWhoseManifestDiffersFromTheReleasesIsRefused() async throws {
      let (kits, current) = try await installFirstKit()
      try publish("v1.4.0")
      releases.serve(TrialKitFixture.manifest("v1.5.0"), as: .latestManifest)
      let zip = base.appendingPathComponent("other.zip")
      let kit = try TrialKitFixture.make(
        at: base.appendingPathComponent("other"), version: "v1.4.0")
      try kit.archive(to: zip)
      releases.serve(try TrialKitFixture.checksum(of: zip), as: .checksum(version("v1.5.0")))
      releases.serve(try Data(contentsOf: zip), as: .archive(version("v1.5.0")))
      try await assertFails(kits, keeping: current)
    }

    func testAnUnsignedOrUntrustedKitIsRefused() async throws {
      let (_, current) = try await installFirstKit()
      try publish("v1.4.0")
      var untrusted = FakeCodeEvaluator()
      untrusted.fallback.team = "AAAAAAAAAA"
      try await assertFails(updater(downloads: untrusted), keeping: current)
      clock.advance(hours: 25)
      untrusted.fallback = FakeCodeEvaluator.Code(valid: false)
      try await assertFails(updater(downloads: untrusted), keeping: current)
    }

    func testAnUnsafeArchiveIsRefused() async throws {
      let (kits, current) = try await installFirstKit()
      try publish("v1.4.0")
      let zip = HandMadeZip.data([
        HandMadeZip.Entry(name: "\(HandMadeZip.top)/../escape", data: Data("x".utf8))
      ])
      releases.serve(zip, as: .archive(version("v1.4.0")))
      releases.serve(
        Data("\(SHA256Hex.of(zip))  TryOmarchyKit.zip\n".utf8), as: .checksum(version("v1.4.0")))
      try await assertFails(kits, keeping: current)
      XCTAssertFalse(
        FileManager.default.fileExists(
          atPath: store.directory.appendingPathComponent("../escape").path))
    }

    func testAPointerOutsideTheFolderIsIgnored() async throws {
      try store.createFolder()
      for target in ["/tmp", "../Helpers", "kit-../../x", "other"] {
        let pointer = store.directory.appendingPathComponent("current")
        try? FileManager.default.removeItem(at: pointer)
        try FileManager.default.createSymbolicLink(
          atPath: pointer.path, withDestinationPath: target)
        XCTAssertNil(store.current(), target)
        XCTAssertEqual(updater().kitForNextStart().layout, embedded, target)
      }
    }

    func testAFetchedKitNoNewerThanTheEmbeddedOneIsNeverRun() async throws {
      try publish("v1.3.0")
      let kits = updater()
      _ = await kits.checkIfDue()
      try FileManager.default.removeItem(at: embedded.root)
      embedded = try TrialKitFixture.make(at: embedded.root, version: "v1.3.0").layout
      XCTAssertEqual(updater().kitForNextStart().layout, embedded)
      XCTAssertEqual(try fetchedKits(), [])
    }

    func testPreferringAKit() throws {
      let release = { (text: String, run: Int) in
        TryOmarchyKitManifest(
          version: TryOmarchyKitVersion(text)!, runProtocolVersion: run, buildDate: Date())
      }
      XCTAssertTrue(TrialKitUpdater.prefers(release("v1.2.1", 1), over: release("v1.2.0", 1)))
      XCTAssertTrue(TrialKitUpdater.prefers(release("v0.0.1", 1), over: nil))
      XCTAssertFalse(TrialKitUpdater.prefers(release("v1.2.0", 1), over: release("v1.2.0", 1)))
      XCTAssertFalse(TrialKitUpdater.prefers(release("v1.1.0", 1), over: release("v1.2.0", 1)))
      XCTAssertFalse(TrialKitUpdater.prefers(release("v9.0.0", 2), over: release("v1.2.0", 1)))
      XCTAssertFalse(TrialKitUpdater.prefers(release("v9.0.0", 1), over: release("development", 1)))
      XCTAssertFalse(TrialKitUpdater.prefers(release("development", 1), over: nil))
    }

    // MARK: Signatures

    private func signedKit(_ code: [String: FakeCodeEvaluator.Code]) throws -> URL {
      let kit = try TrialKitFixture.make(
        at: base.appendingPathComponent("signed"), version: "v1.3.0")
      try TrialKitFixture.machO.write(to: kit.root.appendingPathComponent("libextra.dylib"))
      evaluator.code = code
      return kit.root
    }

    private func verify(_ root: URL, policy: TrialKitSignaturePolicy = .release) throws {
      try TrialKitSignatureCheck(policy: policy, evaluator: evaluator).verify(root)
    }

    func testEveryMachOIsCheckedWhateverTheLayout() throws {
      let root = try signedKit([:])
      let found = try TrialKitSignatureCheck.machOFiles(under: root).map(\.lastPathComponent)
      XCTAssertEqual(Set(found), ["libextra.dylib", TryOmarchyKit.helperName])
      XCTAssertNoThrow(try verify(root))
    }

    func testOneOfThePolicysTeamsSignsTheWholeKit() throws {
      let other = TrialKitSigner(teamIdentifier: "BBBBBBBBBB", codeIdentifier: "dev.example.kit")
      let policy = TrialKitSignaturePolicy.developerID([.tryOmarchy, other])
      let signed = FakeCodeEvaluator.Code(
        team: other.teamIdentifier, identifier: other.codeIdentifier)
      XCTAssertNoThrow(try verify(signedKit([:]), policy: policy))
      try FileManager.default.removeItem(at: base.appendingPathComponent("signed"))
      XCTAssertNoThrow(
        try verify(
          signedKit(["libextra.dylib": signed, TryOmarchyKit.helperName: signed]), policy: policy))
      try FileManager.default.removeItem(at: base.appendingPathComponent("signed"))
      XCTAssertThrowsError(try verify(signedKit(["libextra.dylib": signed]), policy: policy)) {
        XCTAssertEqual($0 as? TrialKitSignatureRefusal, .mixedTeams)
      }
    }

    func testAnotherTeamOrIdentifierIsRefused() throws {
      XCTAssertThrowsError(try verify(signedKit(["libextra.dylib": .init(team: "AAAAAAAAAA")]))) {
        XCTAssertEqual($0 as? TrialKitSignatureRefusal, .mixedTeams)
      }
      try FileManager.default.removeItem(at: base.appendingPathComponent("signed"))
      evaluator.fallback.team = "AAAAAAAAAA"
      let root = try signedKit([:])
      let launcher = TrialKitLayout(root: root).launcherURL.path.dropFirst(root.path.count + 1)
      XCTAssertThrowsError(try verify(root)) {
        XCTAssertEqual($0 as? TrialKitSignatureRefusal, .untrusted(String(launcher)))
      }
      evaluator.fallback = FakeCodeEvaluator.Code()
      try FileManager.default.removeItem(at: base.appendingPathComponent("signed"))
      XCTAssertThrowsError(
        try verify(signedKit(["libextra.dylib": .init(identifier: "dev.tryomarchy.native")])))
      try FileManager.default.removeItem(at: base.appendingPathComponent("signed"))
      XCTAssertThrowsError(try verify(signedKit(["libextra.dylib": .init(team: nil)])))
    }

    func testTheHardenedRuntimeIsRequired() throws {
      XCTAssertThrowsError(try verify(signedKit(["libextra.dylib": .init(runtime: false)]))) {
        XCTAssertEqual($0 as? TrialKitSignatureRefusal, .noHardenedRuntime("libextra.dylib"))
      }
    }

    func testAdHocSignaturesPassOnlyUnderTheTestPolicy() throws {
      let root = try signedKit(["libextra.dylib": .init(team: nil, runtime: false)])
      XCTAssertThrowsError(try verify(root))
      XCTAssertNoThrow(try verify(root, policy: .anyValidSignature))
      evaluator.code["libextra.dylib"]?.valid = false
      XCTAssertThrowsError(try verify(root, policy: .anyValidSignature))
    }

    func testAKitWithNoCodeIsRefused() throws {
      let root = base.appendingPathComponent("empty")
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
      XCTAssertThrowsError(try verify(root)) {
        XCTAssertEqual($0 as? TrialKitSignatureRefusal, .noCode)
      }
    }

    func testTheRequirementNamesDeveloperIDTheTeamAndTheIdentifier() {
      let requirement = TrialKitSigner.tryOmarchy.requirement
      XCTAssertTrue(requirement.hasPrefix("anchor apple generic"))
      XCTAssertTrue(requirement.contains("certificate leaf[subject.OU] = \"RZC79MPD34\""))
      XCTAssertTrue(requirement.contains("identifier \"dev.tryomarchy.kit\""))
      XCTAssertTrue(requirement.contains("1.2.840.113635.100.6.1.13"))
      XCTAssertEqual(TrialKitSignaturePolicy.release, .developerID([.tryOmarchy]))
    }

    func testTheSecurityFrameworkRefusesAdHocCodeUnlessTheTestPolicyAllowsIt() throws {
      let root = base.appendingPathComponent("real")
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
      let tool = root.appendingPathComponent("tool")
      try FileManager.default.copyItem(atPath: "/usr/bin/true", toPath: tool.path)
      try TrialKitFixture.run("/usr/bin/codesign", ["--force", "--sign", "-", tool.path])
      let real = SecurityTrialKitCodeEvaluator()
      XCTAssertEqual(real.check(tool, requirement: nil)?.satisfiesRequirement, true)
      XCTAssertEqual(
        SecurityTrialKitCodeEvaluator(enforcesRevocation: true).check(tool, requirement: nil)?
          .satisfiesRequirement, true)
      XCTAssertEqual(
        real.check(tool, requirement: TrialKitSigner.tryOmarchy.requirement)?.satisfiesRequirement,
        false)
      XCTAssertThrowsError(try TrialKitSignatureCheck(policy: .release).verify(root)) {
        XCTAssertEqual($0 as? TrialKitSignatureRefusal, .untrusted("tool"))
      }
      XCTAssertNoThrow(try TrialKitSignatureCheck(policy: .anyValidSignature).verify(root))
      let handle = try FileHandle(forUpdating: tool)
      try handle.seek(toOffset: 0x4000)
      try handle.write(contentsOf: Data([0xFF, 0xFF, 0xFF, 0xFF]))
      try handle.close()
      XCTAssertNil(real.check(tool, requirement: nil))
      XCTAssertThrowsError(try TrialKitSignatureCheck(policy: .anyValidSignature).verify(root))
    }

    // MARK: Archives

    private func refuses(
      _ entries: [HandMadeZip.Entry], file: StaticString = #filePath, line: UInt = #line
    ) throws {
      let zip = base.appendingPathComponent("\(UUID().uuidString).zip")
      try HandMadeZip.data(entries).write(to: zip)
      XCTAssertThrowsError(try TrialKitArchive.entries(of: zip), file: file, line: line)
      let destination = base.appendingPathComponent(UUID().uuidString)
      XCTAssertThrowsError(
        try TrialKitArchive.unpack(zip, into: destination), file: file, line: line)
      XCTAssertFalse(
        FileManager.default.fileExists(atPath: destination.path), file: file, line: line)
    }

    private func named(_ name: String) -> String { "\(HandMadeZip.top)/\(name)" }

    func testAPlainArchiveUnpacksAsListedWithPrivateModesAndNoAppleDouble() throws {
      let zip = base.appendingPathComponent("plain.zip")
      let firmware = Data(repeating: 7, count: 300_000)
      try HandMadeZip.data([
        .init(name: "\(HandMadeZip.top)/", mode: 0o040755),
        .init(name: named("file"), data: Data("x".utf8)),
        .init(name: named("bin/tool"), mode: 0o100755, data: Data("#!/bin/sh\n".utf8)),
        .init(name: named("firmware.fd"), data: firmware, deflate: true),
        .init(name: named("._file"), data: Data("apple double".utf8)),
      ]).write(to: zip)
      XCTAssertEqual(
        try TrialKitArchive.entries(of: zip).map(\.kind), [.directory, .file, .file, .file, .file])
      let destination = base.appendingPathComponent("plain")
      try TrialKitArchive.unpack(zip, into: destination)
      let kit = destination.appendingPathComponent(HandMadeZip.top)
      XCTAssertEqual(try Data(contentsOf: kit.appendingPathComponent("firmware.fd")), firmware)
      XCTAssertFalse(
        FileManager.default.fileExists(atPath: kit.appendingPathComponent("._file").path))
      let mode = { (path: String) in
        try
          (FileManager.default.attributesOfItem(atPath: kit.appendingPathComponent(path).path)[
            .posixPermissions] as? NSNumber)?.intValue
      }
      XCTAssertEqual(try mode("file"), 0o600)
      XCTAssertEqual(try mode("bin/tool"), 0o700)
      XCTAssertEqual(try mode("bin"), 0o700)
    }

    func testOnlyTheKitsFolderMayBeAtTheTop() throws {
      try refuses([.init(name: "other/file", data: Data("x".utf8))])
      try refuses([.init(name: "file", data: Data("x".utf8))])
      try refuses([.init(name: named("file")), .init(name: "TryOmarchyKit.bundle.extra")])
    }

    func testUnsafeNamesAreRefused() throws {
      for name in [
        "/etc/passwd", "../x", named("../../x"), named("./x"), named("a//x"), named("a\\x"),
        named("x\0y"),
      ] {
        try refuses([.init(name: name, data: Data("x".utf8))])
      }
    }

    func testLinksAndRepeatedNamesAreRefused() throws {
      try refuses([.init(name: named("link"), mode: 0o120755, data: Data("/tmp".utf8))])
      try refuses([.init(name: named("x")), .init(name: named("X"))])
      try refuses([.init(name: named("caf\u{E9}")), .init(name: named("cafe\u{301}"))])
    }

    func testOtherFileTypesModesAndEncryptionAreRefused() throws {
      try refuses([.init(name: named("fifo"), mode: 0o010644)])
      try refuses([.init(name: named("device"), mode: 0o020644)])
      try refuses([.init(name: named("setuid"), mode: 0o104755)])
      try refuses([.init(name: named("writable"), mode: 0o100666)])
      try refuses([.init(name: named("secret"), flags: 1)])
    }

    private func refusesUnpacking(
      _ entries: [HandMadeZip.Entry], file: StaticString = #filePath, line: UInt = #line
    ) throws {
      let zip = base.appendingPathComponent("\(UUID().uuidString).zip")
      try HandMadeZip.data(entries).write(to: zip)
      let destination = base.appendingPathComponent(UUID().uuidString)
      XCTAssertThrowsError(
        try TrialKitArchive.unpack(zip, into: destination), file: file, line: line)
      XCTAssertFalse(
        FileManager.default.fileExists(atPath: destination.path), file: file, line: line)
      XCTAssertFalse(
        FileManager.default.fileExists(atPath: base.appendingPathComponent("escaped.txt").path),
        file: file, line: line)
    }

    func testALocalHeaderThatDisagreesWithTheDirectoryIsRefused() throws {
      try refusesUnpacking([
        .init(name: named("a.txt"), data: Data("x".utf8), localName: "../escaped.txt")
      ])
      try refusesUnpacking([
        .init(name: named("a.txt"), data: Data("x".utf8), localName: named("b.txt"))
      ])
    }

    func testAnEntryMissingFromTheDirectoryIsRefused() throws {
      try refusesUnpacking([
        .init(name: named("a.txt"), data: Data("x".utf8)),
        .init(name: named("hidden"), data: Data("hidden".utf8), listed: false),
        .init(name: named("b.txt"), data: Data("y".utf8)),
      ])
      try refusesUnpacking([
        .init(name: named("a.txt"), data: Data("x".utf8)),
        .init(name: named("hidden"), data: Data("hidden".utf8), listed: false),
      ])
    }

    func testADeflateBombStopsAtItsDeclaredSize() throws {
      let bomb = Data(count: 50 << 20)
      let zip = base.appendingPathComponent("bomb.zip")
      try HandMadeZip.data([
        .init(name: named("bomb"), data: bomb, deflate: true, declaredBytes: 10)
      ]).write(to: zip)
      XCTAssertLessThan(
        try FileManager.default.attributesOfItem(atPath: zip.path)[.size] as! Int, 1 << 20)
      let destination = base.appendingPathComponent("bomb")
      XCTAssertThrowsError(try TrialKitArchive.unpack(zip, into: destination)) {
        XCTAssertEqual($0 as? TrialKitArchive.Refusal, .tooLarge)
      }
      XCTAssertFalse(FileManager.default.fileExists(atPath: destination.path))
    }

    func testAWrongChecksumOrShortEntryIsRefused() throws {
      try refusesUnpacking([.init(name: named("a"), data: Data("abc".utf8), crc: 1)])
      try refusesUnpacking([.init(name: named("a"), data: Data("abc".utf8), declaredBytes: 4)])
      try refusesUnpacking([
        .init(
          name: named("a"), data: Data(repeating: 1, count: 1_000), deflate: true,
          declaredBytes: 2_000)
      ])
    }

    func testANonArchiveIsRefused() throws {
      let zip = base.appendingPathComponent("not.zip")
      try Data("not a zip".utf8).write(to: zip)
      XCTAssertThrowsError(try TrialKitArchive.entries(of: zip))
    }

    func testAnUnpackedLinkOrHardLinkIsRefused() throws {
      let root = base.appendingPathComponent("links")
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
      try FileManager.default.createSymbolicLink(
        atPath: root.appendingPathComponent("link").path, withDestinationPath: "file")
      XCTAssertThrowsError(try TrialKitArchive.checkUnpacked(root))
      try FileManager.default.removeItem(at: root.appendingPathComponent("link"))
      try Data("x".utf8).write(to: root.appendingPathComponent("a"))
      try FileManager.default.linkItem(
        at: root.appendingPathComponent("a"), to: root.appendingPathComponent("b"))
      XCTAssertThrowsError(try TrialKitArchive.checkUnpacked(root)) {
        XCTAssertEqual($0 as? TrialKitArchive.Refusal, .unsafeEntry("a"))
      }
    }

    func testARealArchiveWithALinkIsRefusedBeforeAnythingIsWritten() throws {
      let source = base.appendingPathComponent("source/\(HandMadeZip.top)")
      try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
      try FileManager.default.createSymbolicLink(
        atPath: source.appendingPathComponent("out").path, withDestinationPath: "../../outside")
      let zip = base.appendingPathComponent("link.zip")
      try TrialKitFixture.run(
        "/usr/bin/ditto", ["-c", "-k", source.deletingLastPathComponent().path, zip.path])
      XCTAssertThrowsError(try TrialKitArchive.entries(of: zip))
      let destination = base.appendingPathComponent("unpacked")
      XCTAssertThrowsError(try TrialKitArchive.unpack(zip, into: destination))
      XCTAssertFalse(FileManager.default.fileExists(atPath: destination.path))
    }

    func testADittoArchiveUnpacksWithoutItsAttributes() throws {
      let kit = try TrialKitFixture.make(at: base.appendingPathComponent("q"), version: "v1.3.0")
      let value = Data("0081;00000000;Test;".utf8)
      _ = value.withUnsafeBytes {
        setxattr(
          kit.layout.manifestURL.path, "com.apple.quarantine", $0.baseAddress, value.count, 0, 0)
      }
      let zip = base.appendingPathComponent("q.zip")
      try kit.archive(to: zip)
      XCTAssertTrue(try TrialKitArchive.entries(of: zip).contains { $0.name.contains("/._") })
      let destination = base.appendingPathComponent("unpacked")
      try TrialKitArchive.unpack(zip, into: destination)
      let manifest = TrialKitLayout(root: destination).manifestURL.path
      XCTAssertEqual(getxattr(manifest, "com.apple.quarantine", nil, 0, 0, 0), -1)
      XCTAssertEqual(
        try TrialKitLayout(root: destination).manifest().version, TryOmarchyKitVersion("v1.3.0"))
      XCTAssertTrue(TrialKitLayout(root: destination).hasLauncher)
    }

    // MARK: Releases

    func testReleaseAssetsComeFromTheLatestPublishedRelease() {
      let releases = TrialKitReleases()
      XCTAssertEqual(
        releases.url(for: .latestManifest).absoluteString,
        "https://github.com/omacom/try-omarchy/releases/latest/download/TryOmarchyKit.json")
      XCTAssertEqual(
        releases.url(for: .archive(TryOmarchyKitVersion("v1.3.0")!)).absoluteString,
        "https://github.com/omacom/try-omarchy/releases/download/v1.3.0/TryOmarchyKit.zip")
      XCTAssertEqual(
        releases.url(for: .checksum(TryOmarchyKitVersion("v1.3.0")!)).absoluteString,
        "https://github.com/omacom/try-omarchy/releases/download/v1.3.0/TryOmarchyKit.zip.sha256")
      XCTAssertFalse(releases.allowsLoopbackHTTP)
      XCTAssertFalse(TrialKitReleases(base: URL(string: "http://example.com/")!).allowsLoopbackHTTP)
    }

    func testOnlyDebugBuildsTakeALocalServerOrAdHocSignatures() {
      let settings = TrialKitUpdater.Settings.from(environment: [
        TrialKitUpdater.Settings.releasesKey: "http://127.0.0.1:8123/",
        TrialKitUpdater.Settings.adHocKey: "1",
      ])
      #if DEBUG
        XCTAssertEqual(settings.releases.absoluteString, "http://127.0.0.1:8123/")
        XCTAssertEqual(settings.policy, .anyValidSignature)
      #else
        XCTAssertEqual(settings, .release)
      #endif
      let remote = TrialKitUpdater.Settings.from(environment: [
        TrialKitUpdater.Settings.releasesKey: "http://example.com/"
      ])
      XCTAssertEqual(remote, .release)
      XCTAssertEqual(TrialKitUpdater.Settings.from(environment: [:]), .release)
    }

    func testAKitIsFetchedOverHTTPAndUsedAtTheNextStart() async throws {
      let kit = try TrialKitFixture.make(
        at: base.appendingPathComponent("served"), version: "v1.3.0")
      let zip = base.appendingPathComponent("served.zip")
      try kit.archive(to: zip)
      let server = try LoopbackServer(files: [
        "/latest/download/TryOmarchyKit.json": TrialKitFixture.manifest("v1.3.0"),
        "/download/v1.3.0/TryOmarchyKit.zip.sha256": try TrialKitFixture.checksum(of: zip),
        "/download/v1.3.0/TryOmarchyKit.zip": try Data(contentsOf: zip),
      ])
      let url = try await server.start()
      defer { server.stop() }
      let clock = clock!
      let kits = TrialKitUpdater(
        store: store, embedded: embedded, source: TrialKitReleases(base: url),
        signatures: TrialKitSignatureCheck(policy: .release, evaluator: evaluator),
        network: network, now: { clock.now })
      let outcome = await kits.checkIfDue()
      XCTAssertEqual(outcome, .installed(version("v1.3.0")))
      XCTAssertEqual(try kits.kitForNextStart().layout.manifest().version, version("v1.3.0"))

      clock.advance(hours: 25)
      let missing = TrialKitUpdater(
        store: store, embedded: embedded,
        source: TrialKitReleases(base: url.appendingPathComponent("none/")),
        signatures: TrialKitSignatureCheck(policy: .release, evaluator: evaluator),
        network: network, now: { clock.now })
      let failed = await missing.checkIfDue()
      XCTAssertEqual(failed, .failed("download: httpStatus(404)"))
      XCTAssertEqual(try missing.kitForNextStart().layout.manifest().version, version("v1.3.0"))
    }

    func testARedirectAwayFromHTTPSIsRefusedAsSuch() async throws {
      let server = try LoopbackServer(
        files: [:],
        redirects: ["/latest/download/TryOmarchyKit.json": "http://example.com/TryOmarchyKit.json"])
      let url = try await server.start()
      defer { server.stop() }
      let destination = base.appendingPathComponent("redirected.json")
      do {
        try await TrialKitReleases(base: url).fetch(.latestManifest, to: destination)
        XCTFail("Expected the redirect to be refused")
      } catch {
        XCTAssertEqual(error as? TrialKitDownloadError, .refusedRedirect)
      }
      XCTAssertFalse(FileManager.default.fileExists(atPath: destination.path))
    }

    func testADownloadPastItsLimitIsStopped() async throws {
      let server = try LoopbackServer(files: [
        "/latest/download/TryOmarchyKit.json": Data(count: TryOmarchyKitManifest.maximumBytes + 1)
      ])
      let url = try await server.start()
      defer { server.stop() }
      let destination = base.appendingPathComponent("big.json")
      do {
        try await TrialKitReleases(base: url).fetch(.latestManifest, to: destination)
        XCTFail("Expected the download to stop")
      } catch {
        XCTAssertEqual(error as? TrialKitDownloadError, .tooLarge)
      }
      XCTAssertFalse(FileManager.default.fileExists(atPath: destination.path))
    }
  }

  enum SHA256Hex {
    static func of(_ data: Data) -> String {
      let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      defer { try? FileManager.default.removeItem(at: url) }
      try? data.write(to: url)
      return (try? TrialKitUpdater.digest(ofFile: url)) ?? ""
    }
  }
#endif

#if os(macOS)
  import OmarchyAppleInstallerTrustCore
  import XCTest
  @testable import OmarchyInstallerUXCore

  @MainActor
  final class IPhoneUSBDownloadSessionTests: XCTestCase {
    func testDownloadOptionAppearsOnlyDuringPreparationAndForIPhoneUSB() async throws {
      let environment = MockInstallerEnvironment()
      environment.accountReply = nil
      environment.payloadPrefetchRequired = true
      environment.prefetchGate = OperationGate()
      environment.downloadNetwork.updatePath(
        InstallerNetworkPathSnapshot(
          isSatisfied: true, isExpensive: true, isConstrained: true, iPhoneUSBInterface: "en7"))
      let session = InstallerSession(environment: environment)
      XCTAssertFalse(session.showsIPhoneUSBDownloadOption)
      session.setIPhoneUSBDownloadAllowed(true, for: session.downloadNetwork.iPhoneUSBConnectionID)
      XCTAssertFalse(environment.downloadNetwork.current().allowsIPhoneUSBDownload)
      await session.inspect()
      await session.continueToPlan()
      XCTAssertTrue(session.showsIPhoneUSBDownloadOption)
      XCTAssertFalse(session.downloadNetwork.allowsIPhoneUSBDownload)
      session.setIPhoneUSBDownloadAllowed(true, for: session.downloadNetwork.iPhoneUSBConnectionID)
      XCTAssertTrue(environment.downloadNetwork.current().allowsPrefetch)
      XCTAssertTrue(session.downloadNetwork.allowsIPhoneUSBDownload)
      session.setIPhoneUSBDownloadAllowed(false, for: session.downloadNetwork.iPhoneUSBConnectionID)
      XCTAssertFalse(environment.downloadNetwork.current().allowsPrefetch)

      let observer = Task { await session.observeDownloadNetwork() }
      environment.downloadNetwork.updatePath(
        InstallerNetworkPathSnapshot(isSatisfied: true, isExpensive: true, isConstrained: false))
      try await Task.sleep(for: .milliseconds(30))
      XCTAssertFalse(session.showsIPhoneUSBDownloadOption)
      XCTAssertFalse(session.downloadNetwork.allowsIPhoneUSBDownload)
      observer.cancel()
      await environment.prefetchGate?.release()
      session.cancelPrefetchOnQuit()
    }

    func testClickForAStaleDisplayedConnectionDoesNotAuthorizeTheNewPhoneRoute() async throws {
      let environment = MockInstallerEnvironment()
      environment.accountReply = nil
      environment.payloadPrefetchRequired = true
      environment.prefetchGate = OperationGate()
      environment.downloadNetwork.updatePath(
        InstallerNetworkPathSnapshot(
          isSatisfied: true, isExpensive: true, isConstrained: false, iPhoneUSBInterface: "en7"))
      let session = InstallerSession(environment: environment)
      await session.inspect()
      await session.continueToPlan()
      XCTAssertTrue(session.showsIPhoneUSBDownloadOption)
      let displayedConnection = session.downloadNetwork.iPhoneUSBConnectionID
      let observer = Task { await session.observeDownloadNetwork() }
      defer { observer.cancel() }
      environment.downloadNetwork.updatePath(
        InstallerNetworkPathSnapshot(
          isSatisfied: true, isExpensive: true, isConstrained: false, iPhoneUSBInterface: "en8"))
      try await Task.sleep(for: .milliseconds(30))
      XCTAssertNotEqual(displayedConnection, session.downloadNetwork.iPhoneUSBConnectionID)
      session.setIPhoneUSBDownloadAllowed(true, for: displayedConnection)
      XCTAssertFalse(environment.downloadNetwork.current().allowsPrefetch)
      XCTAssertEqual(session.downloadNetwork.iPhoneUSBInterface, "en8")
      await environment.prefetchGate?.release()
      session.cancelPrefetchOnQuit()
    }

    func testVerifiedPayloadAndUnsupportedHostNeverOfferTheToggle() async {
      for supported in [true, false] {
        let environment = MockInstallerEnvironment()
        environment.accountReply = nil
        environment.host = HostDisplay(chipAndSpace: "Fixture Mac", supported: supported)
        environment.downloadNetwork.updatePath(
          InstallerNetworkPathSnapshot(
            isSatisfied: true, isExpensive: true, isConstrained: false, iPhoneUSBInterface: "en7"))
        let session = InstallerSession(environment: environment)
        await session.inspect()
        await session.continueToPlan()
        XCTAssertFalse(session.showsIPhoneUSBDownloadOption)
        XCTAssertFalse(environment.downloadNetwork.current().allowsIPhoneUSBDownload)
      }
    }
  }
#endif

#if os(macOS)
  import XCTest
  @testable import OmarchyAppleInstallerTrustCore

  final class IPhoneUSBDownloadTests: XCTestCase {
    func testOnlyTheActiveDirectIPhoneUSBRouteIsDetected() {
      XCTAssertEqual(
        IPhoneUSBNetworkInterface.matchingInterface(
          isSatisfied: true, preferredInterface: "en7", defaultInterfaces: ["en7"],
          iPhoneInterfaces: ["en7"]), "en7")
      for routes: Set<String> in [["en0"], ["en1"], ["utun0"], ["en7", "en0"], []] {
        XCTAssertNil(
          IPhoneUSBNetworkInterface.matchingInterface(
            isSatisfied: true, preferredInterface: routes.first, defaultInterfaces: routes,
            iPhoneInterfaces: ["en7"]))
      }
      XCTAssertNil(
        IPhoneUSBNetworkInterface.matchingInterface(
          isSatisfied: false, preferredInterface: "en7", defaultInterfaces: ["en7"],
          iPhoneInterfaces: ["en7"]))
      XCTAssertNil(
        IPhoneUSBNetworkInterface.matchingInterface(
          isSatisfied: true, preferredInterface: "en0", defaultInterfaces: ["en7"],
          iPhoneInterfaces: ["en7"]))
      XCTAssertNil(
        IPhoneUSBNetworkInterface.matchingInterface(
          isSatisfied: true, preferredInterface: "en7", defaultInterfaces: ["en7"],
          iPhoneInterfaces: []))
    }

    func testConsentAppliesOnlyToTheConnectedIPhoneUSBPath() {
      let network = InstallerDownloadNetworkState()
      network.updatePath(phonePath())
      XCTAssertFalse(network.current().allowsPrefetch)
      network.setIPhoneUSBDownloadAllowed(true, for: network.current().iPhoneUSBConnectionID)
      XCTAssertTrue(network.current().allowsPrefetch)
      network.updatePath(phonePath(constrained: true))
      XCTAssertTrue(network.current().allowsPrefetch)

      network.updatePath(
        InstallerNetworkPathSnapshot(isSatisfied: true, isExpensive: true, isConstrained: false))
      XCTAssertFalse(network.current().allowsPrefetch)
      XCTAssertFalse(network.current().allowsIPhoneUSBDownload)
      network.setIPhoneUSBDownloadAllowed(true, for: network.current().iPhoneUSBConnectionID)
      XCTAssertFalse(network.current().allowsPrefetch)
      network.updatePath(phonePath())
      XCTAssertFalse(network.current().allowsIPhoneUSBDownload)
    }

    func testDisconnectAndDifferentUSBInterfaceRequireNewConsent() {
      let network = InstallerDownloadNetworkState()
      network.updatePath(phonePath())
      network.setIPhoneUSBDownloadAllowed(true, for: network.current().iPhoneUSBConnectionID)
      network.updatePath(phonePath(interface: "en8"))
      XCTAssertFalse(network.current().allowsIPhoneUSBDownload)
      network.setIPhoneUSBDownloadAllowed(true, for: network.current().iPhoneUSBConnectionID)
      network.updatePath(phonePath(interface: "en8", satisfied: false))
      XCTAssertFalse(network.current().allowsIPhoneUSBDownload)
      network.updatePath(phonePath(interface: "en8"))
      XCTAssertFalse(network.current().allowsPrefetch)
      XCTAssertFalse(InstallerDownloadNetworkState().current().allowsIPhoneUSBDownload)
    }

    func testStaleToggleCannotAuthorizeAnotherConnectionOrAReconnectedPhone() {
      let network = InstallerDownloadNetworkState()
      network.updatePath(phonePath())
      let oldConnection = network.current().iPhoneUSBConnectionID
      network.updatePath(phonePath(interface: "en8"))
      network.setIPhoneUSBDownloadAllowed(true, for: oldConnection)
      XCTAssertFalse(network.current().allowsPrefetch)
      network.updatePath(phonePath(satisfied: false))
      network.updatePath(phonePath())
      network.setIPhoneUSBDownloadAllowed(true, for: oldConnection)
      XCTAssertFalse(network.current().allowsPrefetch)
      network.setIPhoneUSBDownloadAllowed(true, for: nil)
      XCTAssertFalse(network.current().allowsPrefetch)
    }

    func testToggleResumesWaitingDownloadAndRevokingConsentPausesIt() async throws {
      let network = InstallerDownloadNetworkState()
      network.updatePath(phonePath())
      let waiting = expectation(description: "Waits for iPhone USB consent")
      let paused = expectation(description: "Revoked consent pauses the transfer")
      let started = expectation(description: "Transfer starts after consent")
      let controller = PayloadPrefetchController(
        network: network, freeSpace: TestFreeSpace(), keepAwake: TestKeepAwake(),
        onState: { state in
          if state == .waitingForUnmeteredNetwork { waiting.fulfill() }
          if case .paused = state { paused.fulfill() }
        })
      await controller.start(requiredBytes: 100) {
        await controller.reportDownload(completed: 1, total: 100)
        started.fulfill()
        try await Task.sleep(for: .seconds(10))
      }
      await fulfillment(of: [waiting], timeout: 2)
      network.setIPhoneUSBDownloadAllowed(true, for: network.current().iPhoneUSBConnectionID)
      await fulfillment(of: [started], timeout: 2)
      network.setIPhoneUSBDownloadAllowed(false, for: network.current().iPhoneUSBConnectionID)
      await fulfillment(of: [paused], timeout: 2)
      await controller.cancel()
    }

    private func phonePath(
      interface: String = "en7", satisfied: Bool = true, constrained: Bool = false
    ) -> InstallerNetworkPathSnapshot {
      InstallerNetworkPathSnapshot(
        isSatisfied: satisfied, isExpensive: true, isConstrained: constrained,
        iPhoneUSBInterface: interface)
    }
  }

  private struct TestFreeSpace: InstallerFreeSpaceChecking {
    func availableBytes() throws -> UInt64 { 8_000_000_000 }
  }

  private struct TestKeepAwake: InstallerKeepAwakeHolding {
    func acquire() {}
    func release() {}
  }
#endif

#if os(macOS)
  import Foundation

  /// In-memory consent shared by the download and UI. A different route or a
  /// disconnected phone requires a new opt-in; no preference is saved.
  public final class InstallerDownloadNetworkState:
    InstallerNetworkPathObserving, @unchecked Sendable
  {
    private let lock = NSLock()
    private var last = InstallerNetworkPathSnapshot(
      isSatisfied: false, isExpensive: true, isConstrained: true)
    private var continuations: [UUID: AsyncStream<InstallerNetworkPathSnapshot>.Continuation] = [:]

    public init() {}

    public func current() -> InstallerNetworkPathSnapshot {
      lock.withLock { last }
    }

    public func updatePath(_ path: InstallerNetworkPathSnapshot) {
      lock.withLock {
        let sameConnection =
          path.usesIPhoneUSB && last.usesIPhoneUSB
          && path.iPhoneUSBInterface == last.iPhoneUSBInterface
        let connectionID =
          path.usesIPhoneUSB
          ? (sameConnection ? last.iPhoneUSBConnectionID : UUID()) : nil
        publish(
          path, connectionID: connectionID,
          allowed: sameConnection && last.allowsIPhoneUSBDownload)
      }
    }

    public func setIPhoneUSBDownloadAllowed(_ allowed: Bool, for connectionID: UUID?) {
      lock.withLock {
        // A click queued for an older connection must not authorize a new one.
        guard !allowed || (connectionID != nil && connectionID == last.iPhoneUSBConnectionID)
        else { return }
        publish(
          last, connectionID: last.iPhoneUSBConnectionID,
          allowed: allowed && last.usesIPhoneUSB)
      }
    }

    public func updates() -> AsyncStream<InstallerNetworkPathSnapshot> {
      AsyncStream { continuation in
        let id = UUID()
        continuation.onTermination = { [weak self] _ in
          self?.lock.withLock { self?.continuations[id] = nil }
        }
        lock.withLock {
          continuations[id] = continuation
          continuation.yield(last)
        }
      }
    }

    /// Called under the lock so path changes and toggle events are delivered
    /// in the same order to every subscriber.
    private func publish(
      _ path: InstallerNetworkPathSnapshot, connectionID: UUID?, allowed: Bool
    ) {
      last = InstallerNetworkPathSnapshot(
        isSatisfied: path.isSatisfied, isExpensive: path.isExpensive,
        isConstrained: path.isConstrained, iPhoneUSBInterface: path.iPhoneUSBInterface,
        iPhoneUSBConnectionID: connectionID,
        allowsIPhoneUSBDownload: allowed)
      for continuation in continuations.values {
        continuation.yield(last)
      }
    }
  }
#endif

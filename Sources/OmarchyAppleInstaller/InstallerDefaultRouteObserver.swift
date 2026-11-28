#if os(macOS)
  import Foundation
  import SystemConfiguration

  /// PrimaryInterface can change after NWPath's callback. Observe the store
  /// too, so a temporarily inconsistent route does not hide the USB option.
  final class InstallerDefaultRouteObserver {
    private let store: SCDynamicStore

    init?(queue: DispatchQueue, onChange: @escaping @Sendable () -> Void) {
      let callback = Callback(onChange: onChange)
      var context = SCDynamicStoreContext(
        version: 0, info: Unmanaged.passUnretained(callback).toOpaque(),
        retain: { info in
          return UnsafeRawPointer(Unmanaged<Callback>.fromOpaque(info).retain().toOpaque())
        },
        release: { info in
          Unmanaged<Callback>.fromOpaque(info).release()
        }, copyDescription: nil)
      guard
        let store = SCDynamicStoreCreate(
          nil, "org.omarchy.installer.network" as CFString,
          { _, _, info in
            guard let info else { return }
            Unmanaged<Callback>.fromOpaque(info).takeUnretainedValue().onChange()
          }, &context)
      else { return nil }
      let keys = ["State:/Network/Global/IPv4", "State:/Network/Global/IPv6"]
      guard SCDynamicStoreSetNotificationKeys(store, keys as CFArray, nil),
        SCDynamicStoreSetDispatchQueue(store, queue)
      else { return nil }
      self.store = store
    }

    deinit {
      SCDynamicStoreSetDispatchQueue(store, nil)
    }

    private final class Callback {
      let onChange: @Sendable () -> Void
      init(onChange: @escaping @Sendable () -> Void) { self.onChange = onChange }
    }
  }
#endif

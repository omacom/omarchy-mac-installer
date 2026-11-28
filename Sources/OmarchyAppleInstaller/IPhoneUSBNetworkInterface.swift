#if os(macOS)
  import Foundation
  import IOKit
  import Network
  import SystemConfiguration

  enum IPhoneUSBNetworkInterface {
    static func activeInterface(on path: NWPath) -> String? {
      matchingInterface(
        isSatisfied: path.status == .satisfied,
        preferredInterface: path.availableInterfaces.first?.name,
        defaultInterfaces: defaultRouteInterfaces(), iPhoneInterfaces: iPhoneUSBInterfaces())
    }

    /// Available interfaces include attached adapters that are not carrying
    /// internet traffic. Require one consistent default route, including IPv6.
    static func matchingInterface(
      isSatisfied: Bool, preferredInterface: String?, defaultInterfaces: Set<String>,
      iPhoneInterfaces: Set<String>
    ) -> String? {
      guard isSatisfied, defaultInterfaces.count == 1,
        let active = defaultInterfaces.first, active == preferredInterface,
        iPhoneInterfaces.contains(active)
      else { return nil }
      return active
    }

    private static func defaultRouteInterfaces() -> Set<String> {
      let keys = ["State:/Network/Global/IPv4", "State:/Network/Global/IPv6"]
      guard let values = SCDynamicStoreCopyMultiple(nil, keys as CFArray, nil) as? [String: Any]
      else { return [] }
      return Set(
        values.values.compactMap {
          ($0 as? [String: Any])?[kSCDynamicStorePropNetPrimaryInterface as String] as? String
        })
    }

    private static func iPhoneUSBInterfaces() -> Set<String> {
      var controllers: io_iterator_t = 0
      guard let matching = IOServiceMatching("AppleUSBEthernetHost"),
        IOServiceGetMatchingServices(kIOMainPortDefault, matching, &controllers) == KERN_SUCCESS
      else { return [] }
      defer { IOObjectRelease(controllers) }

      var names = Set<String>()
      while case let controller = IOIteratorNext(controllers), controller != 0 {
        defer { IOObjectRelease(controller) }
        // The same driver serves iPads. Use the USB product identity, not a
        // localized or user-editable network service name, to identify iPhone.
        let product =
          IORegistryEntrySearchCFProperty(
            controller, kIOServicePlane, "USB Product Name" as CFString, kCFAllocatorDefault,
            IOOptionBits(kIORegistryIterateRecursively | kIORegistryIterateParents)) as? String
        guard product == "iPhone" else { continue }

        var descendants: io_iterator_t = 0
        guard
          IORegistryEntryCreateIterator(
            controller, kIOServicePlane, IOOptionBits(kIORegistryIterateRecursively), &descendants)
            == KERN_SUCCESS
        else { continue }
        defer { IOObjectRelease(descendants) }
        while case let entry = IOIteratorNext(descendants), entry != 0 {
          defer { IOObjectRelease(entry) }
          guard IOObjectConformsTo(entry, "IOEthernetInterface") != 0,
            let name = IORegistryEntryCreateCFProperty(
              entry, "BSD Name" as CFString, kCFAllocatorDefault, 0)?.takeRetainedValue() as? String
          else { continue }
          names.insert(name)
        }
      }
      return names
    }
  }
#endif

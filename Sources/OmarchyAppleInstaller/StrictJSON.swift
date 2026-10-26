import Foundation

/// A JSON value read without the leniencies of `JSONSerialization`: duplicate
/// object keys, non-UTF-8 input, trailing bytes and unbounded nesting are
/// errors, and integers stay distinct from other numbers and from booleans.
public enum StrictJSONValue: Equatable, Sendable {
  case object([String: StrictJSONValue])
  case array([StrictJSONValue])
  case string(String)
  case integer(Int64)
  /// A number with a fraction or exponent, or an integer outside `Int64`.
  case otherNumber
  case boolean(Bool)
  case null
}

public enum StrictJSONError: Error, Equatable, Sendable {
  case invalidUTF8
  case malformed(offset: Int)
  case duplicateKey(String)
  case tooDeep
}

public enum StrictJSON {
  public static let maximumDepth = 64

  public static func parse(_ data: Data) throws -> StrictJSONValue {
    guard String(data: data, encoding: .utf8) != nil else {
      throw StrictJSONError.invalidUTF8
    }
    var parser = Parser(bytes: Array(data))
    parser.skipWhitespace()
    let value = try parser.value(depth: 0)
    parser.skipWhitespace()
    guard parser.offset == parser.bytes.count else {
      throw StrictJSONError.malformed(offset: parser.offset)
    }
    return value
  }

  private struct Parser {
    let bytes: [UInt8]
    var offset = 0

    init(bytes: [UInt8]) {
      self.bytes = bytes
    }

    mutating func skipWhitespace() {
      while offset < bytes.count,
        [0x20, 0x09, 0x0A, 0x0D].contains(bytes[offset])
      {
        offset += 1
      }
    }

    func malformed() -> StrictJSONError {
      .malformed(offset: offset)
    }

    mutating func value(depth: Int) throws -> StrictJSONValue {
      guard offset < bytes.count else {
        throw malformed()
      }
      switch bytes[offset] {
      case UInt8(ascii: "{"):
        return try object(depth: depth + 1)
      case UInt8(ascii: "["):
        return try array(depth: depth + 1)
      case UInt8(ascii: "\""):
        return .string(try string())
      case UInt8(ascii: "t"):
        try literal("true")
        return .boolean(true)
      case UInt8(ascii: "f"):
        try literal("false")
        return .boolean(false)
      case UInt8(ascii: "n"):
        try literal("null")
        return .null
      default:
        return try number()
      }
    }

    mutating func literal(_ word: String) throws {
      let expected = Array(word.utf8)
      guard bytes.count - offset >= expected.count,
        Array(bytes[offset..<offset + expected.count]) == expected
      else {
        throw malformed()
      }
      offset += expected.count
    }

    mutating func object(depth: Int) throws -> StrictJSONValue {
      guard depth <= StrictJSON.maximumDepth else {
        throw StrictJSONError.tooDeep
      }
      offset += 1
      var members: [String: StrictJSONValue] = [:]
      skipWhitespace()
      if offset < bytes.count, bytes[offset] == UInt8(ascii: "}") {
        offset += 1
        return .object(members)
      }
      while true {
        skipWhitespace()
        guard offset < bytes.count, bytes[offset] == UInt8(ascii: "\"") else {
          throw malformed()
        }
        let key = try string()
        guard members[key] == nil else {
          throw StrictJSONError.duplicateKey(key)
        }
        skipWhitespace()
        guard offset < bytes.count, bytes[offset] == UInt8(ascii: ":") else {
          throw malformed()
        }
        offset += 1
        skipWhitespace()
        members[key] = try value(depth: depth)
        skipWhitespace()
        guard offset < bytes.count else {
          throw malformed()
        }
        if bytes[offset] == UInt8(ascii: ",") {
          offset += 1
          continue
        }
        guard bytes[offset] == UInt8(ascii: "}") else {
          throw malformed()
        }
        offset += 1
        return .object(members)
      }
    }

    mutating func array(depth: Int) throws -> StrictJSONValue {
      guard depth <= StrictJSON.maximumDepth else {
        throw StrictJSONError.tooDeep
      }
      offset += 1
      var elements: [StrictJSONValue] = []
      skipWhitespace()
      if offset < bytes.count, bytes[offset] == UInt8(ascii: "]") {
        offset += 1
        return .array(elements)
      }
      while true {
        skipWhitespace()
        elements.append(try value(depth: depth))
        skipWhitespace()
        guard offset < bytes.count else {
          throw malformed()
        }
        if bytes[offset] == UInt8(ascii: ",") {
          offset += 1
          continue
        }
        guard bytes[offset] == UInt8(ascii: "]") else {
          throw malformed()
        }
        offset += 1
        return .array(elements)
      }
    }

    mutating func string() throws -> String {
      offset += 1
      var scalars = String.UnicodeScalarView()
      var run: [UInt8] = []

      func flush() throws {
        guard !run.isEmpty else { return }
        guard let text = String(bytes: run, encoding: .utf8) else {
          throw StrictJSONError.invalidUTF8
        }
        scalars.append(contentsOf: text.unicodeScalars)
        run.removeAll(keepingCapacity: true)
      }

      while offset < bytes.count {
        let byte = bytes[offset]
        switch byte {
        case UInt8(ascii: "\""):
          offset += 1
          try flush()
          return String(scalars)
        case UInt8(ascii: "\\"):
          try flush()
          offset += 1
          guard offset < bytes.count else {
            throw malformed()
          }
          let escape = bytes[offset]
          offset += 1
          switch escape {
          case UInt8(ascii: "\""): scalars.append("\"")
          case UInt8(ascii: "\\"): scalars.append("\\")
          case UInt8(ascii: "/"): scalars.append("/")
          case UInt8(ascii: "b"): scalars.append("\u{08}")
          case UInt8(ascii: "f"): scalars.append("\u{0C}")
          case UInt8(ascii: "n"): scalars.append("\n")
          case UInt8(ascii: "r"): scalars.append("\r")
          case UInt8(ascii: "t"): scalars.append("\t")
          case UInt8(ascii: "u"):
            scalars.append(try unicodeEscape())
          default:
            throw malformed()
          }
        case 0x00..<0x20:
          throw malformed()
        default:
          run.append(byte)
          offset += 1
        }
      }
      throw malformed()
    }

    mutating func hexQuad() throws -> UInt32 {
      guard bytes.count - offset >= 4 else {
        throw malformed()
      }
      var value: UInt32 = 0
      for byte in bytes[offset..<offset + 4] {
        let digit: UInt8
        switch byte {
        case UInt8(ascii: "0")...UInt8(ascii: "9"): digit = byte - UInt8(ascii: "0")
        case UInt8(ascii: "a")...UInt8(ascii: "f"): digit = byte - UInt8(ascii: "a") + 10
        case UInt8(ascii: "A")...UInt8(ascii: "F"): digit = byte - UInt8(ascii: "A") + 10
        default: throw malformed()
        }
        value = value << 4 | UInt32(digit)
      }
      offset += 4
      return value
    }

    mutating func unicodeEscape() throws -> Unicode.Scalar {
      let first = try hexQuad()
      if (0xD800...0xDBFF).contains(first) {
        guard bytes.count - offset >= 2,
          bytes[offset] == UInt8(ascii: "\\"),
          bytes[offset + 1] == UInt8(ascii: "u")
        else {
          throw malformed()
        }
        offset += 2
        let second = try hexQuad()
        guard (0xDC00...0xDFFF).contains(second),
          let scalar = Unicode.Scalar(0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00))
        else {
          throw malformed()
        }
        return scalar
      }
      guard let scalar = Unicode.Scalar(first) else {
        throw malformed()
      }
      return scalar
    }

    mutating func number() throws -> StrictJSONValue {
      let start = offset
      if offset < bytes.count, bytes[offset] == UInt8(ascii: "-") {
        offset += 1
      }
      let digitsStart = offset
      while offset < bytes.count, (0x30...0x39).contains(bytes[offset]) {
        offset += 1
      }
      let integerDigits = offset - digitsStart
      guard integerDigits > 0,
        integerDigits == 1 || bytes[digitsStart] != UInt8(ascii: "0")
      else {
        throw StrictJSONError.malformed(offset: start)
      }
      var integral = true
      if offset < bytes.count, bytes[offset] == UInt8(ascii: ".") {
        integral = false
        offset += 1
        let fractionStart = offset
        while offset < bytes.count, (0x30...0x39).contains(bytes[offset]) {
          offset += 1
        }
        guard offset > fractionStart else {
          throw malformed()
        }
      }
      if offset < bytes.count, [UInt8(ascii: "e"), UInt8(ascii: "E")].contains(bytes[offset]) {
        integral = false
        offset += 1
        if offset < bytes.count, [UInt8(ascii: "+"), UInt8(ascii: "-")].contains(bytes[offset]) {
          offset += 1
        }
        let exponentStart = offset
        while offset < bytes.count, (0x30...0x39).contains(bytes[offset]) {
          offset += 1
        }
        guard offset > exponentStart else {
          throw malformed()
        }
      }
      guard integral,
        let text = String(bytes: bytes[start..<offset], encoding: .ascii),
        let integer = Int64(text)
      else {
        return .otherNumber
      }
      return .integer(integer)
    }
  }
}

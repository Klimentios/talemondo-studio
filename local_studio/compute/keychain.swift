import Foundation
import Security

let args = CommandLine.arguments
guard args.count == 3 else { exit(64) }

let action = args[1]
let provider = args[2]
// "hf": the Hugging Face token under the service the GPU install scripts read (security -s h3-hf-token).
guard ["vast", "hf"].contains(provider), ["set", "get", "delete"].contains(action) else { exit(64) }

let service = provider == "hf" ? "h3-hf-token" : "com.studio.local-studio.compute"
let account = provider == "hf" ? "h3" : provider
let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: service,
    kSecAttrAccount as String: account,
    kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
    kSecAttrSynchronizable as String: false
]

switch action {
case "set":
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty, data.count <= 8192 else { exit(65) }
    let attributes: [String: Any] = [kSecValueData as String: data]
    let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
    if updateStatus == errSecItemNotFound {
        var item = query
        item[kSecValueData as String] = data
        let addStatus = SecItemAdd(item as CFDictionary, nil)
        guard addStatus == errSecSuccess else { exit(1) }
    } else if updateStatus != errSecSuccess {
        exit(1)
    }

case "get":
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(lookup as CFDictionary, &result)
    if status == errSecItemNotFound { exit(2) }
    guard status == errSecSuccess, let data = result as? Data else { exit(1) }
    FileHandle.standardOutput.write(data)

case "delete":
    let status = SecItemDelete(query as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else { exit(1) }

default:
    exit(64)
}

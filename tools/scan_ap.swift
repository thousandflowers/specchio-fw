import CoreWLAN
import Foundation
// Passive look for one network name; does not leave the current network.
let want = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "specchio-setup"
guard let iface = CWWiFiClient.shared().interface() else { print("no wifi interface"); exit(2) }
do {
    let found = try iface.scanForNetworks(withName: want)
    print("named scan for \(want): \(found.count) network(s)")
    for n in found { print("  ch \(n.wlanChannel?.channelNumber ?? 0) rssi \(n.rssiValue) ssid \(n.ssid ?? "<hidden by macOS>")") }
    let all = try iface.scanForNetworks(withName: nil)
    print("all networks in range: \(all.count), with a readable name: \(all.filter { $0.ssid != nil }.count)")
} catch { print("scan failed: \(error.localizedDescription)") }

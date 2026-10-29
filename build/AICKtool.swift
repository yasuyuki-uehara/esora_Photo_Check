import Foundation

/// AICKtool.app の入口。Mach-O バイナリとしてビルドする。
/// （シェルスクリプトを CFBundleExecutable にすると、新しい macOS では起動が拒否される）

func die(_ message: String) -> Never {
    FileHandle.standardError.write(Data("AICKtool: \(message)\n".utf8))
    let log = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/AICKtool.log")
    let line = "\(Date()): \(message)\n"
    if let data = line.data(using: .utf8) {
        if FileManager.default.fileExists(atPath: log.path),
           let handle = try? FileHandle(forWritingTo: log) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: log)
        }
    }
    exit(1)
}

func bashSingleQuoted(_ path: String) -> String {
    "'" + path.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

let exe = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
let resources = exe
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .appendingPathComponent("Resources")

let override = ProcessInfo.processInfo.environment["AICKTOOL_LAUNCH_SCRIPT"]
let scriptURL: URL
if let override, !override.isEmpty {
    scriptURL = URL(fileURLWithPath: override)
} else {
    scriptURL = resources.appendingPathComponent("launch.sh")
}

guard FileManager.default.isReadableFile(atPath: scriptURL.path) else {
    die("起動スクリプトが見つかりません: \(scriptURL.path)")
}

// 一時的な .command を作り、Terminal で開く。
// （このアプリ自身が作るファイルなので、ダウンロードの警告は出ない）
let tmp = FileManager.default.temporaryDirectory
    .appendingPathComponent("AICKtool-\(UUID().uuidString).command")
let body = """
#!/bin/bash
rm -f "$0"
exec /bin/bash \(bashSingleQuoted(scriptURL.path))
"""
do {
    try body.write(to: tmp, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o755],
        ofItemAtPath: tmp.path
    )
} catch {
    die("一時ファイルを作れませんでした: \(error.localizedDescription)")
}

let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
process.arguments = ["-a", "Terminal", tmp.path]
do {
    try process.run()
    process.waitUntilExit()
} catch {
    die("Terminal を開けませんでした: \(error.localizedDescription)")
}

if process.terminationStatus != 0 {
    die("Terminal の起動に失敗しました（終了コード \(process.terminationStatus)）")
}
exit(0)

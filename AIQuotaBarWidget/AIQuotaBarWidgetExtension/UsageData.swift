import Foundation

struct UsageSnapshot: Codable {
    let version: Int
    let updatedAt: String
    let claude: ClaudeUsage
    let chatgpt: ChatGPTUsage
    let claudeCode: ClaudeCodeUsage

    enum CodingKeys: String, CodingKey {
        case version
        case updatedAt = "updated_at"
        case claude, chatgpt
        case claudeCode = "claude_code"
    }
}

struct ClaudeUsage: Codable {
    let session: LimitRow?
    let weeklyAll: LimitRow?
    let weeklySonnet: LimitRow?
    let overagesEnabled: Bool?

    enum CodingKeys: String, CodingKey {
        case session
        case weeklyAll = "weekly_all"
        case weeklySonnet = "weekly_sonnet"
        case overagesEnabled = "overages_enabled"
    }
}

struct ChatGPTUsage: Codable {
    let rows: [LimitRow]?
    let error: String?
}

struct ClaudeCodeUsage: Codable {
    let todayMessages: Int
    let weekMessages: Int

    enum CodingKeys: String, CodingKey {
        case todayMessages = "today_messages"
        case weekMessages = "week_messages"
    }
}

struct LimitRow: Codable {
    let label: String
    let pct: Int
    let resetStr: String

    enum CodingKeys: String, CodingKey {
        case label, pct
        case resetStr = "reset_str"
    }
}

// MARK: - File reading

enum UsageDataReader {
    static let fileURL: URL = {
        // Resolve real home via POSIX (NSHomeDirectory returns sandbox container)
        let pw = getpwuid(getuid())
        let home = pw.flatMap { String(cString: $0.pointee.pw_dir) } ?? "/tmp"
        return URL(fileURLWithPath: home)
            .appendingPathComponent("Library/Application Support/AIQuotaBar/usage.json")
    }()

    static func read() -> UsageSnapshot? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? JSONDecoder().decode(UsageSnapshot.self, from: data)
    }

    /// Returns true if the data is older than the given interval (seconds).
    static func isStale(_ snapshot: UsageSnapshot, threshold: TimeInterval = 1800) -> Bool {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = formatter.date(from: snapshot.updatedAt) else {
            // Try without fractional seconds
            let basic = ISO8601DateFormatter()
            guard let d = basic.date(from: snapshot.updatedAt) else { return true }
            return Date().timeIntervalSince(d) > threshold
        }
        return Date().timeIntervalSince(date) > threshold
    }
}

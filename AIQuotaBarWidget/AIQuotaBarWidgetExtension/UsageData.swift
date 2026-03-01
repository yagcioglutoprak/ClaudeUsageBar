import Foundation

struct UsageSnapshot: Codable {
    let version: Int
    let updatedAt: String
    let claude: ClaudeUsage
    let chatgpt: ChatGPTUsage
    let claudeCode: ClaudeCodeUsage
    let cursor: CursorUsage?
    let copilot: CopilotUsage?
    let activeProviders: [String]?
    let barProviders: [String]?

    enum CodingKeys: String, CodingKey {
        case version
        case updatedAt = "updated_at"
        case claude, chatgpt, cursor, copilot
        case claudeCode = "claude_code"
        case activeProviders = "active_providers"
        case barProviders = "bar_providers"
    }

    /// Providers to display, respecting user's bar config or auto-detecting top 2.
    var detectedProviders: [AIProvider] {
        // 1. User's explicit bar choice (set via Status Bar menu in the app)
        if let bar = barProviders, !bar.isEmpty {
            let parsed = bar.compactMap { AIProvider(rawValue: $0) }
            if !parsed.isEmpty { return parsed }
        }
        // 2. Auto: top 2 active by priority
        let priority: [AIProvider] = [.claude, .chatgpt, .cursor, .copilot]
        guard let ids = activeProviders, !ids.isEmpty else {
            return [.claude, .chatgpt]
        }
        let active = Set(ids.compactMap { AIProvider(rawValue: $0) })
        let picked = priority.filter { active.contains($0) }
        let result = Array(picked.prefix(2))
        return result.isEmpty ? [.claude, .chatgpt] : result
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

struct CursorUsage: Codable {
    let rows: [LimitRow]?
    let error: String?
}

struct CopilotUsage: Codable {
    let spent: Double?
    let limit: Double?
    let pct: Int?
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

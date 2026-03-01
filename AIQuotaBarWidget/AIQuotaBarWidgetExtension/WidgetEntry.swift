import WidgetKit

struct QuotaEntry: TimelineEntry {
    let date: Date
    let snapshot: UsageSnapshot?
    let isStale: Bool
    let providers: [AIProvider]

    static let placeholder = QuotaEntry(
        date: .now,
        snapshot: UsageSnapshot(
            version: 1,
            updatedAt: ISO8601DateFormatter().string(from: .now),
            claude: ClaudeUsage(
                session: LimitRow(label: "Current Session", pct: 36, resetStr: "resets in 2h 14m"),
                weeklyAll: LimitRow(label: "All Models", pct: 83, resetStr: "resets Wed 23:00"),
                weeklySonnet: nil,
                overagesEnabled: false
            ),
            chatgpt: ChatGPTUsage(
                rows: [LimitRow(label: "Codex Tasks", pct: 12, resetStr: "resets Thu 05:38")],
                error: nil
            ),
            claudeCode: ClaudeCodeUsage(todayMessages: 42, weekMessages: 312),
            cursor: nil,
            copilot: nil,
            activeProviders: ["claude", "chatgpt"],
            barProviders: nil
        ),
        isStale: false,
        providers: [.claude, .chatgpt]
    )

    static let empty = QuotaEntry(
        date: .now,
        snapshot: nil,
        isStale: false,
        providers: [.claude, .chatgpt]
    )
}

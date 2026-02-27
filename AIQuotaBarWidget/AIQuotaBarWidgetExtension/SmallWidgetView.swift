import SwiftUI
import WidgetKit

struct SmallWidgetView: View {
    let entry: QuotaEntry

    // Brand colors
    private let claudeColor = Color(red: 0.85, green: 0.55, blue: 0.35)   // warm terracotta
    private let chatgptColor = Color(red: 0.45, green: 0.78, blue: 0.65)  // mint green

    var body: some View {
        if let snap = entry.snapshot {
            dataView(snap)
                .containerBackground(.fill.tertiary, for: .widget)
        } else {
            noDataView
                .containerBackground(.fill.tertiary, for: .widget)
        }
    }

    private func dataView(_ snap: UsageSnapshot) -> some View {
        let claudePct = snap.claude.session?.pct ?? 0
        let gptPct = snap.chatgpt.rows?.map(\.pct).max() ?? 0

        return HStack(spacing: 0) {
            // Claude
            VStack(spacing: 6) {
                Image("claude_icon")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 28, height: 28)
                Text("\(claudePct)%")
                    .font(.system(size: 24, weight: .medium, design: .rounded))
                    .foregroundStyle(colorForPct(claudePct, accent: claudeColor))
            }
            .frame(maxWidth: .infinity)

            // ChatGPT
            VStack(spacing: 6) {
                Image("chatgpt_icon")
                    .renderingMode(.template)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 28, height: 28)
                    .foregroundStyle(chatgptColor)
                Text("\(gptPct)%")
                    .font(.system(size: 24, weight: .medium, design: .rounded))
                    .foregroundStyle(colorForPct(gptPct, accent: chatgptColor))
            }
            .frame(maxWidth: .infinity)
        }
    }

    private var noDataView: some View {
        VStack(spacing: 6) {
            Image(systemName: "chart.bar")
                .font(.title2)
                .foregroundStyle(.tertiary)
            Text("No Data")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func colorForPct(_ pct: Int, accent: Color) -> Color {
        if pct >= 95 { return .red }
        if pct >= 80 { return .orange }
        return accent
    }
}

import SwiftUI
import WidgetKit

struct MediumWidgetView: View {
    let entry: QuotaEntry

    // Brand colors
    private let claudeColor = Color(red: 0.85, green: 0.55, blue: 0.35)   // warm terracotta
    private let chatgptColor = Color(red: 0.45, green: 0.78, blue: 0.65)  // mint green

    var body: some View {
        if let snap = entry.snapshot {
            contentView(snap)
                .containerBackground(.fill.tertiary, for: .widget)
        } else {
            noDataView
                .containerBackground(.fill.tertiary, for: .widget)
        }
    }

    private func contentView(_ snap: UsageSnapshot) -> some View {
        HStack(spacing: 0) {
            // ── Claude ──
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 6) {
                    Image("claude_icon")
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 22, height: 22)
                    Text("Claude")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(claudeColor)
                }

                if let session = snap.claude.session {
                    limitRow(session, accent: claudeColor)
                }
                if let weekly = snap.claude.weeklyAll {
                    limitRow(weekly, accent: claudeColor)
                }

                if snap.claudeCode.todayMessages > 0 {
                    HStack(spacing: 3) {
                        Image(systemName: "terminal")
                            .font(.system(size: 8))
                        Text("\(snap.claudeCode.todayMessages) msgs today")
                            .font(.system(size: 10))
                    }
                    .foregroundStyle(claudeColor.opacity(0.7))
                }
            }
            .padding(.trailing, 12)
            .frame(maxWidth: .infinity, alignment: .leading)

            // Divider
            Rectangle()
                .fill(.quaternary)
                .frame(width: 1)
                .padding(.vertical, 2)

            // ── ChatGPT ──
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 6) {
                    Image("chatgpt_icon")
                        .renderingMode(.template)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 22, height: 22)
                        .foregroundStyle(chatgptColor)
                    Text("ChatGPT")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(chatgptColor)
                }

                if let error = snap.chatgpt.error {
                    Text(error)
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                } else if let rows = snap.chatgpt.rows {
                    ForEach(rows.prefix(3), id: \.label) { row in
                        limitRow(row, accent: chatgptColor)
                    }
                } else {
                    Text("Not set up")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.leading, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func limitRow(_ row: LimitRow, accent: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline) {
                Text(row.label)
                    .font(.system(size: 12))
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text("\(row.pct)%")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundStyle(colorForPct(row.pct, accent: accent))
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary)
                    Capsule()
                        .fill(colorForPct(row.pct, accent: accent).opacity(0.85))
                        .frame(width: max(2, geo.size.width * CGFloat(row.pct) / 100))
                }
            }
            .frame(height: 4)

            Text(row.resetStr)
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private var noDataView: some View {
        VStack(spacing: 6) {
            Image(systemName: "chart.bar")
                .font(.title2)
                .foregroundStyle(.secondary)
            Text("Run AIQuotaBar to see usage")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func colorForPct(_ pct: Int, accent: Color) -> Color {
        if pct >= 95 { return .red }
        if pct >= 80 { return .orange }
        return accent
    }
}

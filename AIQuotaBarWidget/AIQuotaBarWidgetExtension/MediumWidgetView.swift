import SwiftUI
import WidgetKit

struct MediumWidgetView: View {
    let entry: QuotaEntry

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
        let providers = entry.providers
        let maxRows = providers.count > 2 ? 2 : 3

        return HStack(spacing: 0) {
            ForEach(Array(providers.enumerated()), id: \.offset) { idx, provider in
                if idx > 0 {
                    Rectangle()
                        .fill(.quaternary)
                        .frame(width: 1)
                        .padding(.vertical, 2)
                }
                providerColumn(snap: snap, provider: provider, maxRows: maxRows)
                    .padding(.leading, idx > 0 ? 10 : 0)
                    .padding(.trailing, idx < providers.count - 1 ? 10 : 0)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func providerColumn(snap: UsageSnapshot, provider: AIProvider, maxRows: Int) -> some View {
        let data = provider.displayData(from: snap)
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                providerIcon(provider, size: 22)
                Text(provider.displayName)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(provider.color)
            }

            if let error = data.error {
                Text(error)
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            } else if data.isConfigured {
                ForEach(data.rows.prefix(maxRows), id: \.label) { row in
                    limitRow(row, accent: provider.color)
                }
                if let extra = data.extraInfo {
                    HStack(spacing: 3) {
                        Image(systemName: "terminal")
                            .font(.system(size: 8))
                        Text(extra)
                            .font(.system(size: 10))
                    }
                    .foregroundStyle(provider.color.opacity(0.7))
                }
            } else {
                Text("Not set up")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func providerIcon(_ provider: AIProvider, size: CGFloat) -> some View {
        if provider.iconNeedsTemplate {
            Image(provider.iconName)
                .renderingMode(.template)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
                .foregroundStyle(provider.color)
        } else {
            Image(provider.iconName)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
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

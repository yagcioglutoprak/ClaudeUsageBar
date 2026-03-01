import SwiftUI
import WidgetKit

struct SmallWidgetView: View {
    let entry: QuotaEntry

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
        let providers = entry.providers
        let iconSize: CGFloat = providers.count > 2 ? 22 : 28
        let fontSize: CGFloat = providers.count > 2 ? 18 : 24

        return HStack(spacing: 0) {
            ForEach(Array(providers.enumerated()), id: \.offset) { _, provider in
                providerGauge(snap: snap, provider: provider, iconSize: iconSize, fontSize: fontSize)
                    .frame(maxWidth: .infinity)
            }
        }
    }

    private func providerGauge(snap: UsageSnapshot, provider: AIProvider,
                               iconSize: CGFloat, fontSize: CGFloat) -> some View {
        let data = provider.displayData(from: snap)
        return VStack(spacing: 6) {
            providerIcon(provider, size: iconSize)
            Text("\(data.mainPct)%")
                .font(.system(size: fontSize, weight: .medium, design: .rounded))
                .foregroundStyle(colorForPct(data.mainPct, accent: provider.color))
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

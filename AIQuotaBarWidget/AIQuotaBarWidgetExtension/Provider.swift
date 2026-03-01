import WidgetKit

struct QuotaProvider: AppIntentTimelineProvider {
    typealias Intent = SelectProvidersIntent
    typealias Entry = QuotaEntry

    func placeholder(in context: Context) -> QuotaEntry {
        .placeholder
    }

    func snapshot(for configuration: SelectProvidersIntent, in context: Context) async -> QuotaEntry {
        makeEntry(configuration: configuration)
    }

    func timeline(for configuration: SelectProvidersIntent, in context: Context) async -> Timeline<QuotaEntry> {
        let entry = makeEntry(configuration: configuration)
        let next = Calendar.current.date(byAdding: .minute, value: 15, to: .now) ?? .now
        return Timeline(entries: [entry], policy: .after(next))
    }

    private func makeEntry(configuration: SelectProvidersIntent) -> QuotaEntry {
        guard let snapshot = UsageDataReader.read() else {
            let providers = configuration.activeProviders
            return QuotaEntry(date: .now, snapshot: nil, isStale: false,
                              providers: providers.isEmpty ? [.claude, .chatgpt] : providers)
        }

        // If user hasn't customized widget, auto-detect from what the Python app found
        let providers: [AIProvider]
        if configuration.isUsingDefaults {
            providers = snapshot.detectedProviders
        } else {
            let explicit = configuration.activeProviders
            providers = explicit.isEmpty ? [.claude, .chatgpt] : explicit
        }

        let stale = UsageDataReader.isStale(snapshot)
        return QuotaEntry(date: .now, snapshot: snapshot, isStale: stale, providers: providers)
    }
}

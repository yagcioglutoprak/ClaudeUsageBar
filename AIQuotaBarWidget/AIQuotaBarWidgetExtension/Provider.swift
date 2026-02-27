import WidgetKit

struct QuotaProvider: TimelineProvider {
    typealias Entry = QuotaEntry

    func placeholder(in context: Context) -> QuotaEntry {
        .placeholder
    }

    func getSnapshot(in context: Context, completion: @escaping (QuotaEntry) -> Void) {
        completion(makeEntry())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<QuotaEntry>) -> Void) {
        let entry = makeEntry()
        // Refresh every 15 minutes
        let next = Calendar.current.date(byAdding: .minute, value: 15, to: .now) ?? .now
        completion(Timeline(entries: [entry], policy: .after(next)))
    }

    private func makeEntry() -> QuotaEntry {
        guard let snapshot = UsageDataReader.read() else {
            return .empty
        }
        let stale = UsageDataReader.isStale(snapshot)
        return QuotaEntry(date: .now, snapshot: snapshot, isStale: stale)
    }
}

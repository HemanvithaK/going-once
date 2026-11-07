import { services } from './config'

// A component is a function that returns what to show. React calls it to draw the page.
export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
          <h1 className="text-xl font-bold tracking-tight">going-once</h1>
          <span className="text-sm text-slate-500">live auctions</span>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-10">
        <p className="text-lg">Auctions will appear here.</p>
        <p className="mt-2 text-sm text-slate-500">
          The page is running. Next it learns to log in and list auctions.
        </p>
      </main>

      <footer className="mx-auto max-w-5xl px-4 pb-8 text-xs text-slate-400">
        api {services.api} · bidding {services.bidding} · realtime {services.realtime}
      </footer>
    </div>
  )
}
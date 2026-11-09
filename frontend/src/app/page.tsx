export default function Home() {
  return (
    <main className="flex flex-col items-center justify-center min-h-screen px-4 py-16 text-center">
      <div className="max-w-2xl p-8 bg-white rounded-xl shadow-sm border border-slate-200">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
          ClinCoherence
        </h1>
        <p className="mt-3 text-base text-slate-600 sm:text-lg">
          Longitudinal Clinical Coherence in Multi-Turn Clinical LLM Conversations
        </p>

        <div className="mt-6 inline-flex items-center gap-2 px-3 py-1 text-sm font-medium text-emerald-800 bg-emerald-100 rounded-full">
          <span className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
          Phase 0 — Foundation / Scaffolding
        </div>

        <p className="mt-6 text-xs text-slate-400">
          Backend health endpoint implemented (/health) &bull; Static scaffolding placeholder (no live backend check)
        </p>
      </div>
    </main>
  );
}

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="max-w-3xl text-center">
        <h1 className="text-5xl font-bold tracking-tight text-slate-900 dark:text-white sm:text-6xl">
          DevPilot{" "}
          <span className="text-primary-500">🚀</span>
        </h1>
        <p className="mt-6 text-lg leading-8 text-slate-600 dark:text-slate-300">
          Autonomous Multi-Agent Software Engineering Platform.
          Accept a GitHub issue and watch specialized AI agents
          plan, implement, test, review, document, and prepare
          pull requests — like an entire engineering team at your
          command.
        </p>

        <div className="mt-10 flex items-center justify-center gap-x-6">
          <a
            href="#"
            className="rounded-md bg-primary-600 px-6 py-3 text-sm font-semibold text-white shadow-sm hover:bg-primary-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600 transition-colors"
          >
            Get Started →
          </a>
          <a
            href="#"
            className="text-sm font-semibold leading-6 text-slate-900 dark:text-white hover:text-primary-600 dark:hover:text-primary-400 transition-colors"
          >
            Learn more <span aria-hidden="true">→</span>
          </a>
        </div>

        <div className="mt-16 grid grid-cols-1 gap-6 sm:grid-cols-3">
          {[
            {
              title: "Multi-Agent",
              desc: "Specialized agents work together like a human engineering team — from planning to PR.",
            },
            {
              title: "Provider Agnostic",
              desc: "Swap between OpenAI, Anthropic, or any LLM provider without changing agent code.",
            },
            {
              title: "Human-in-the-Loop",
              desc: "Safety gates require human approval before any consequential write operations.",
            },
          ].map((feature) => (
            <div
              key={feature.title}
              className="rounded-xl border border-slate-200 dark:border-slate-700 p-6 text-left hover:border-primary-300 dark:hover:border-primary-600 transition-colors"
            >
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">
                {feature.title}
              </h3>
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
                {feature.desc}
              </p>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}

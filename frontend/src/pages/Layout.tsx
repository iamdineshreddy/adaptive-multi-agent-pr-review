import { NavLink, Outlet } from "react-router-dom";

const NAV_LINKS = [
  { to: "/", label: "Dashboard" },
  { to: "/reviews", label: "Reviews" },
  { to: "/repositories", label: "Repositories" },
  { to: "/metrics", label: "Metrics" },
] as const;

export default function Layout() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center gap-6 overflow-x-auto px-6 py-4">
          <h1 className="whitespace-nowrap text-lg font-semibold tracking-tight">
            PR Review Dashboard
          </h1>
          <nav className="flex gap-4 text-sm font-medium">
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  isActive
                    ? "text-blue-700 underline underline-offset-4"
                    : "text-slate-600 hover:text-slate-900"
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
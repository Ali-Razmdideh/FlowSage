import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function Shell() {
  return (
    <div className="flex h-screen overflow-hidden bg-background text-on-background">
      <Sidebar />
      <main className="flex-1 md:ml-72 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-6 py-8 md:px-10">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

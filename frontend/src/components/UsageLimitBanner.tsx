import { Link } from "react-router-dom";

export function UsageLimitBanner({ message }: { message: string | null }) {
  if (message === null) return null;

  return (
    <div className="rounded-xl border-l-4 border-error bg-error-container/20 p-4 flex items-center justify-between gap-4 flex-wrap">
      <p className="text-sm text-on-error-container">{message}</p>
      <Link
        to="/settings/billing"
        className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium hover:opacity-90 transition whitespace-nowrap"
      >
        Upgrade
      </Link>
    </div>
  );
}

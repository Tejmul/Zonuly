/* The rights line, one place, used by every page footer. */
export function Rights({ className = "" }: { className?: string }) {
  return (
    <p className={`text-[11.5px] text-ink-3 ${className}`}>
      © {new Date().getFullYear()} Tejmul Movin. All rights reserved.
    </p>
  );
}

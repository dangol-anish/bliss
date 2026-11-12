import { cn } from "./cn.js";

export function NativeSelect({ className, ...props }) {
  return (
    <div className="relative min-w-0">
      <select data-slot="native-select"
        className={cn("peer block w-full appearance-none pr-8 forced-colors:appearance-auto", className)}
        {...props} />
      <span aria-hidden="true"
        className="pointer-events-none absolute top-1/2 right-3.5 size-1.5 -translate-y-[65%] rotate-45 border-r border-b border-current text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden" />
    </div>
  );
}

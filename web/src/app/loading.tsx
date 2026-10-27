import { Tx } from "@/i18n/tx";
export default function Loading() {
  return (
    <main className="grid min-h-screen place-items-center p-6">
      <p role="status" aria-live="polite">
        {" "}
        <Tx text="Loading…" />{" "}
      </p>
    </main>
  );
}

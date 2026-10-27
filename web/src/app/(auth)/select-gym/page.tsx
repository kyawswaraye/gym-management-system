import { pageMetadata } from "@/i18n/metadata";
import { Tx } from "@/i18n/tx";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { BrandLogo } from "@/components/brand-logo";
import { GymSelectionForm } from "@/features/auth/gym-selection-form";
import { AUTH_COOKIES } from "@/lib/auth/cookies";
import type { GymSummary } from "@/types/api";

export async function generateMetadata() {
  return pageMetadata("Select gym");
}

function readChoices(raw: string | undefined): GymSummary[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as {
      gyms?: GymSummary[];
      expires_at?: string | null;
    };
    if (parsed.expires_at && Date.parse(parsed.expires_at) <= Date.now()) {
      return [];
    }
    return Array.isArray(parsed.gyms) ? parsed.gyms : [];
  } catch {
    return [];
  }
}

export default async function SelectGymPage() {
  const store = await cookies();
  const ticket = store.get(AUTH_COOKIES.selectionTicket)?.value;
  const gyms = readChoices(store.get(AUTH_COOKIES.selectionChoices)?.value);
  if (!ticket || gyms.length === 0) {
    redirect("/login?reason=expired");
  }

  return (
    <main className="grid w-full flex-1 place-items-center px-6 py-10">
      <section className="card" aria-labelledby="select-gym-title">
        <BrandLogo />
        <h1 id="select-gym-title" className="mt-6 text-3xl font-bold">
          {" "}
          <Tx text="Choose a gym" />{" "}
        </h1>
        <p className="muted mt-2">
          {" "}
          <Tx text="Your session will be scoped to one authorized gym. This choice expires in five minutes." />{" "}
        </p>
        <GymSelectionForm gyms={gyms} />
      </section>
    </main>
  );
}

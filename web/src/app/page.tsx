import { redirect } from "next/navigation";

import { sessionHomePath } from "@/lib/auth/authorization";
import { readCurrentUser } from "@/lib/auth/session";

export default async function HomePage() {
  const session = await readCurrentUser();
  redirect(session ? sessionHomePath(session) : "/login");
}

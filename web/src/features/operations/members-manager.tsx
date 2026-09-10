"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { operationGet, operationMutation } from "@/features/operations/client";
import type { Member } from "@/types/api";

export function MembersManager({ canCreate }: { canCreate: boolean }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    try {
      const query = search ? `?search=${encodeURIComponent(search)}` : "";
      setMembers(await operationGet<Member[]>(`members/${query}`));
      setError("");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to load members.",
      );
    }
  }, [search]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const form = new FormData(event.currentTarget);
    try {
      await operationMutation<Member>("members/", {
        first_name: form.get("first_name"),
        last_name: form.get("last_name"),
        phone: form.get("phone"),
        email: form.get("email"),
        join_date: form.get("join_date"),
      });
      event.currentTarget.reset();
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to create member.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[22rem_1fr]">
      {canCreate && (
        <form className="card grid gap-4 p-5" onSubmit={submit}>
          <h2 className="text-lg font-bold">Add member</h2>
          <label className="field">
            First name
            <input className="input" name="first_name" required />
          </label>
          <label className="field">
            Last name
            <input className="input" name="last_name" />
          </label>
          <label className="field">
            Phone
            <input className="input" name="phone" />
          </label>
          <label className="field">
            Email
            <input className="input" name="email" type="email" />
          </label>
          <label className="field">
            Join date
            <input
              className="input"
              defaultValue={new Date().toISOString().slice(0, 10)}
              name="join_date"
              required
              type="date"
            />
          </label>
          <button className="button" disabled={pending} type="submit">
            {pending ? "Saving…" : "Save member"}
          </button>
        </form>
      )}
      <section className="card overflow-hidden">
        <div className="flex flex-wrap gap-3 border-b border-slate-200 p-4">
          <input
            aria-label="Search members"
            className="input max-w-sm"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search name, number, phone"
            value={search}
          />
        </div>
        {error && (
          <p className="error m-4" role="alert">
            {error}
          </p>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="p-3">Member</th>
                <th className="p-3">Phone</th>
                <th className="p-3">Joined</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr className="border-t border-slate-100" key={member.id}>
                  <td className="p-3">
                    <Link
                      className="font-semibold underline"
                      href={`/members/${member.id}`}
                    >
                      {member.display_name}
                    </Link>
                    <span className="muted block">{member.member_number}</span>
                  </td>
                  <td className="p-3">{member.phone || "—"}</td>
                  <td className="p-3">{member.join_date}</td>
                  <td className="p-3">{member.status}</td>
                </tr>
              ))}
              {!members.length && (
                <tr>
                  <td className="muted p-6 text-center" colSpan={4}>
                    No members found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

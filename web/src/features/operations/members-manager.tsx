"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import Link from "next/link";

import { operationGet, operationMutation } from "@/features/operations/client";
import type { Member } from "@/types/api";

export function MembersManager({
  canCreate,
  defaultDate,
}: {
  canCreate: boolean;
  defaultDate: string;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [joinedFrom, setJoinedFrom] = useState("");
  const [joinedTo, setJoinedTo] = useState("");
  const requestVersion = useRef(0);

  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("search", search.trim());
      if (status) params.set("status", status);
      if (joinedFrom) params.set("joined_from", joinedFrom);
      if (joinedTo) params.set("joined_to", joinedTo);
      const query = params.size ? `?${params.toString()}` : "";
      const rows = await operationGet<Member[]>(`members/${query}`);
      if (version === requestVersion.current) {
        setMembers(rows);
        setError("");
      }
    } catch (caught) {
      if (version === requestVersion.current) {
        setError(
          caught instanceof Error ? caught.message : "Unable to load members.",
        );
      }
    }
  }, [joinedFrom, joinedTo, search, status]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 250);
    return () => {
      window.clearTimeout(timer);
      requestVersion.current += 1;
    };
  }, [load]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await operationMutation<Member>("members/", {
        first_name: form.get("first_name"),
        last_name: form.get("last_name"),
        phone: form.get("phone"),
        email: form.get("email"),
        join_date: form.get("join_date"),
      });
      formElement.reset();
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
              defaultValue={defaultDate}
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
            maxLength={100}
            placeholder="Search name, number, phone, email"
            value={search}
          />
          <select
            aria-label="Filter by status"
            className="input max-w-48"
            onChange={(event) => setStatus(event.target.value)}
            value={status}
          >
            <option value="">All statuses</option>
            <option value="ACTIVE">Active</option>
            <option value="EXPIRED">Expired</option>
            <option value="SUSPENDED">Suspended</option>
            <option value="CANCELLED">Cancelled</option>
            <option value="ARCHIVED">Archived</option>
          </select>
          <input
            aria-label="Joined from"
            className="input max-w-44"
            max={joinedTo || undefined}
            onChange={(event) => {
              const value = event.target.value;
              setJoinedFrom(value);
              if (joinedTo && value > joinedTo) setJoinedTo("");
            }}
            type="date"
            value={joinedFrom}
          />
          <input
            aria-label="Joined to"
            className="input max-w-44"
            min={joinedFrom || undefined}
            onChange={(event) => setJoinedTo(event.target.value)}
            type="date"
            value={joinedTo}
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
                      {`${member.first_name} ${member.last_name}`.trim()}
                      {member.is_pt_client && (
                        <span className="ml-2 rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">
                          PT
                        </span>
                      )}
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

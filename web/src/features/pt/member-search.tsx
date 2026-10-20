"use client";

import { useState, type MouseEvent } from "react";

import { phase5Error } from "@/features/pt/errors";
import { listMembers } from "@/features/operations/client";
import { Field, TextInput } from "@/features/finance/ui";
import type { Member } from "@/types/api";

export type PickedMember = Pick<
  Member,
  "id" | "member_number" | "first_name" | "last_name"
>;

export function memberLabel(member: PickedMember): string {
  const name = `${member.first_name} ${member.last_name}`.trim();
  return name ? `${name} (${member.member_number})` : member.member_number;
}

export function MemberSearch({
  selected,
  onSelect,
  disabled,
  label = "Search active members",
  action = "Search members",
}: {
  selected: PickedMember | null;
  onSelect: (member: PickedMember) => void;
  disabled?: boolean;
  label?: string;
  action?: string;
}) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<PickedMember[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const page = await listMembers({
        search,
        status: "ACTIVE",
        limit: 25,
      });
      setResults(page.data);
      if (page.data.length === 0) setError("No active members match.");
    } catch (caught) {
      setResults([]);
      setError(phase5Error(caught, "Members could not be searched."));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-end gap-3">
        <Field label={label}>
          <TextInput
            name="member_search"
            onChange={(event) => setSearch(event.target.value)}
            value={search}
          />
        </Field>
        <button
          className="button"
          disabled={disabled || pending}
          onClick={(event) => void submit(event)}
          type="button"
        >
          {action}
        </button>
      </div>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {selected ? <p>Selected member: {memberLabel(selected)}</p> : null}
      {results.length > 0 ? (
        <ul className="grid gap-2">
          {results.map((member) => (
            <li key={member.id}>
              <button
                className="underline"
                disabled={disabled}
                onClick={() => onSelect(member)}
                type="button"
              >
                {memberLabel(member)}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

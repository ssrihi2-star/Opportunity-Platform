"use client";

import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { Topic, TopicEntity, Entity } from "@/lib/types";

export default function TopicsPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

function Body() {
  const { t, token, user } = useApp();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newMemberTopicId, setNewMemberTopicId] = useState<string | null>(null);
  const [selectedEntityId, setSelectedEntityId] = useState<string>("");
  const [justification, setJustification] = useState<string>("");

  const load = useCallback(() => {
    if (!token) return;
    api<Topic[]>("/topics", { token })
      .then(setTopics)
      .catch((e) => setError(e.message));
    api<Entity[]>("/entities?limit=1000", { token })
      .then(setEntities)
      .catch((e) => setError(e.message));
  }, [token]);

  useEffect(load, [load]);

  async function addMember(topicId: string) {
    if (!selectedEntityId || !justification.trim()) return;
    setBusy(`${topicId}-add`);
    setError(null);
    try {
      await api(`/topics/${topicId}/members`, {
        method: "POST",
        token,
        body: JSON.stringify({
          entity_id: selectedEntityId,
          justification: justification.trim(),
        }),
      });
      setNewMemberTopicId(null);
      setSelectedEntityId("");
      setJustification("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(null);
    }
  }

  async function removeMember(topicId: string, entityId: string) {
    if (!confirm("Remove this entity from the topic?")) return;
    setBusy(`${topicId}-${entityId}`);
    setError(null);
    try {
      await api(`/topics/${topicId}/members/${entityId}`, {
        method: "DELETE",
        token,
      });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(null);
    }
  }

  const isAdmin = user?.role === "admin";

  return (
    <>
      <h1 className="text-xl font-semibold mb-4">{t.topics?.title || "Topics"}</h1>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-800">
          {error}
        </div>
      )}

      <div className="space-y-4">
        {topics.map((topic) => (
          <Card
            key={topic.id}
            title={topic.label}
            note={topic.description || undefined}
            actions={
              <button
                onClick={() => setExpanded(expanded === topic.id ? null : topic.id)}
                className="btn-ghost text-xs"
              >
                {expanded === topic.id ? "▾" : "▸"}
              </button>
            }
          >
            <div className="flex flex-wrap gap-2 mb-2">
              {topic.category && (
                <StatusBadge status="active" label={topic.category} />
              )}
              <span className="text-xs text-[var(--text-muted)]">
                {topic.entities.length} member{topic.entities.length !== 1 ? "s" : ""}
              </span>
            </div>

            {topic.keywords.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {topic.keywords.map((kw) => (
                  <span
                    key={kw}
                    className="text-xs px-2 py-0.5 bg-[var(--surface-2)] rounded"
                  >
                    {kw}
                  </span>
                ))}
              </div>
            )}

            {expanded && (
              <div className="mt-4 space-y-3 border-t pt-4">
                <h3 className="text-sm font-semibold">Members</h3>
                <div className="space-y-2">
                  {topic.entities.map((member) => (
                    <div
                      key={member.entity_id}
                      className="flex items-start gap-2 text-sm"
                    >
                      <div className="flex-1">
                        <div className="font-medium">{member.entity_name}</div>
                        <div className="text-xs text-[var(--text-muted)]">
                          {member.entity_type}
                          {member.is_manual && (
                            <>
                              {" · "}
                              <span className="text-blue-600">manual</span>
                              {member.justification && (
                                <>
                                  {" · "}
                                  <span className="italic">
                                    {member.justification}
                                  </span>
                                </>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                      {isAdmin && (
                        <button
                          onClick={() => removeMember(topic.id, member.entity_id)}
                          disabled={busy === `${topic.id}-${member.entity_id}`}
                          className="btn-ghost text-xs"
                        >
                          {busy === `${topic.id}-${member.entity_id}` ? "..." : "Remove"}
                        </button>
                      )}
                    </div>
                  ))}
                </div>

                {isAdmin && (
                  <>
                    {newMemberTopicId === topic.id ? (
                      <div className="space-y-2 border-t pt-3">
                        <h4 className="text-sm font-semibold">Add Member</h4>
                        <select
                          value={selectedEntityId}
                          onChange={(e) => setSelectedEntityId(e.target.value)}
                          className="w-full p-2 border rounded text-sm"
                        >
                          <option value="">Select an entity...</option>
                          {entities
                            .filter(
                              (e) =>
                                !topic.entities.some((m) => m.entity_id === e.id)
                            )
                            .map((e) => (
                              <option key={e.id} value={e.id}>
                                {e.canonical_name} ({e.entity_type})
                              </option>
                            ))}
                        </select>
                        <textarea
                          value={justification}
                          onChange={(e) => setJustification(e.target.value)}
                          placeholder="Justification for this membership..."
                          className="w-full p-2 border rounded text-sm"
                          rows={2}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => addMember(topic.id)}
                            disabled={
                              !selectedEntityId ||
                              !justification.trim() ||
                              busy === `${topic.id}-add`
                            }
                            className="btn text-sm"
                          >
                            {busy === `${topic.id}-add` ? "Adding..." : "Add"}
                          </button>
                          <button
                            onClick={() => {
                              setNewMemberTopicId(null);
                              setSelectedEntityId("");
                              setJustification("");
                            }}
                            className="btn-ghost text-sm"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => setNewMemberTopicId(topic.id)}
                        className="btn-ghost text-sm"
                      >
                        + Add Member
                      </button>
                    )}
                  </>
                )}
              </div>
            )}
          </Card>
        ))}
      </div>
    </>
  );
}

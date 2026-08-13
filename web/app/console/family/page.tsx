"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { ErrorMessage, InlineResult, Loading, SectionError } from "@/components/page-state";
import { api } from "@/lib/api";
import type { UsageProfile } from "@/lib/types";

function YouthProfileCard({ profile, onSaved }: { profile: UsageProfile; onSaved: (profile: UsageProfile) => void }) {
  const [form, setForm] = useState({
    memory_consent: profile.memory_consent,
    quiet_start: profile.quiet_start,
    quiet_end: profile.quiet_end,
    daily_limit_minutes: profile.daily_limit_minutes,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await api<UsageProfile>(`/v1/profiles/${profile.id}/guardian-controls`, {
        method: "PATCH",
        body: JSON.stringify(form),
      });
      onSaved(updated);
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit}>
      <div className="split">
        <div>
          <h2>{profile.display_name}</h2>
          <div className="hint">{profile.age_band === "12_13" ? "12–13 岁" : "14–17 岁"}</div>
        </div>
        <span className="status warning">家庭档案</span>
      </div>
      <label className="check">
        <input type="checkbox" checked={form.memory_consent} onChange={(event) => setForm({ ...form, memory_consent: event.target.checked })} />
        允许保存可查看、可删除的摘要记忆
      </label>
      <div className="grid two">
        <label className="field">安静时段开始<input type="time" value={form.quiet_start} onChange={(event) => setForm({ ...form, quiet_start: event.target.value })} /></label>
        <label className="field">安静时段结束<input type="time" value={form.quiet_end} onChange={(event) => setForm({ ...form, quiet_end: event.target.value })} /></label>
      </div>
      <label className="field">每日使用上限（分钟）<input type="number" min={30} max={180} value={form.daily_limit_minutes} onChange={(event) => setForm({ ...form, daily_limit_minutes: Number(event.target.value) })} /></label>
      <p className="hint">连续使用 30 分钟的休息提醒为强制安全项，不能关闭。</p>
      <ErrorMessage message={error} />
      {saved ? <InlineResult>家庭规则已保存，将从下一轮对话生效。</InlineResult> : null}
      <button className="button" type="submit" disabled={saving}>{saving ? "正在保存…" : "保存家庭规则"}</button>
    </form>
  );
}

export default function FamilyPage() {
  const [profiles, setProfiles] = useState<UsageProfile[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setProfiles(await api<UsageProfile[]>("/v1/profiles"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "家庭档案暂时无法加载");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  if (!profiles && !error) return <Loading cards={2} />;
  if (!profiles) return <SectionError message={error ?? "家庭档案暂时无法加载"} onRetry={() => void load()} />;

  const replaceProfile = (updated: UsageProfile) => {
    setProfiles((current) => current?.map((item) => item.id === updated.id ? updated : item) ?? null);
  };

  return (
    <>
      <header className="page-head"><div><div className="eyebrow">Family preview</div><h1>家庭管理</h1><p>成年人可以直接使用；12–17 岁仅能由监护人账户管理。</p></div></header>
      <InlineResult tone="warning">家庭模式仍处于内部白名单阶段。法律、安全和监护流程完成前，不能公开创建或宣传青少年档案；当前宣传与协议继续保持 18+。</InlineResult>
      <div className="grid two" style={{ marginTop: 18 }}>
        {profiles.map((profile) => profile.kind === "youth" ? (
          <YouthProfileCard profile={profile} onSaved={replaceProfile} key={profile.id} />
        ) : (
          <section className="card stack" key={profile.id}>
            <div className="split"><h2>{profile.display_name}</h2><span className="status online">成人档案</span></div>
            <p className="muted">成人使用不受家庭安静时段和青少年每日限额影响。</p>
          </section>
        ))}
        <section className="card soft"><h2>灰度开放后的默认保护</h2><p className="muted">只保存 12–13/14–17 年龄段；22:00–07:00 默认不可用；每日 90 分钟；连续 30 分钟提醒休息。不保存精确生日，不使用声纹。</p></section>
      </div>
    </>
  );
}

"use client";

import Link from "next/link";
import { useState } from "react";

export function SetupGuide({ wifiOnly = false }: { wifiOnly?: boolean }) {
  const [step, setStep] = useState(0);
  const steps = ["准备设备", "连接设备热点", "设置家庭网络", "返回并确认"];
  return <div className="stack">
    <h2>{wifiOnly ? "更换设备 Wi-Fi" : "先让设备连上网络"}</h2>
    <p className="muted">{wifiOnly ? "更换网络会暂时离线，助手和账户绑定会保留，不需要解除绑定。" : "使用手机完成下面几步，再扫描设备屏幕上的绑定二维码。"}</p>
    <nav className="setup-steps" aria-label="配网操作步骤">{steps.map((title, index) => <button type="button" key={title} aria-current={index === step ? "step" : undefined} onClick={() => setStep(index)}><span>{index + 1}</span>{title}</button>)}</nav>
    <p className="hint">这是操作指引，不是设备检测结果。可以点选步骤；请以设备屏幕和账户中的在线状态为准。</p>
    <ol className="setup-instructions">
      <li hidden={step !== 0}><strong>{wifiOnly ? "保持设备通电，进入配网模式。" : "插电，等设备显示配网提示。"}</strong><p>把手机和设备放在路由器附近，准备好家庭 Wi-Fi 密码。</p><p className="hint">需要重新配网时，等设备待机后长按 BOOT 约 2 秒。若一直无法进入待机，重新上电，在启动阶段短按 BOOT 进入配网。</p></li>
      <li hidden={step !== 1}><strong>在手机的 Wi-Fi 设置中连接 Xiaozhi-XXXX。</strong><p>暂时离开浏览器，打开手机「设置 → Wi-Fi」，选择设备屏幕提示的热点。</p><p className="hint">这是设备的临时热点。手机提示“无法上网”时选择仍然连接。保留此页面，不要刷新；此时无法访问云端是正常现象。</p></li>
      <li hidden={step !== 2}><strong>打开设备配网页，选择家里的 2.4GHz Wi-Fi。</strong><p className="hint">在设备页面输入密码并保存，等待连接成功。Wi-Fi 密码只交给设备，本网站不收集。</p><a className="button secondary" href="http://192.168.4.1" target="_blank" rel="noopener noreferrer">打开设备配网页</a><p className="hint">没有自动弹出页面时，可在浏览器输入 http://192.168.4.1。打不开时，先确认手机仍连接设备热点。</p></li>
      <li hidden={step !== 3}><strong>等配网页显示成功，再回到本页。</strong><p>将手机重新连接家里的 Wi-Fi 或移动网络，然后继续。</p><p className="hint">{wifiOnly ? "到设备页检查是否恢复在线。配置和绑定关系保持不变。" : "扫描设备屏幕上的绑定二维码，或输入 6 位备用码，将设备认领到你的账户。"} 配网成功还不代表云端在线。</p>{wifiOnly ? <Link className="button" href="/console/devices">返回检查设备在线状态</Link> : <Link className="button" href="/claim">输入绑定码，认领设备</Link>}</li>
    </ol>
    <div className="split"><button className="button secondary" type="button" disabled={step === 0} onClick={() => setStep(step - 1)}>上一步</button><span className="hint">操作 {step + 1} / 4</span><button className="button" type="button" disabled={step === 3} onClick={() => setStep(step + 1)}>下一步</button></div>
    <details className="details"><summary>找不到热点或连接失败</summary><div className="stack"><p>确认设备通电，并按上方步骤重新进入配网。看不到家庭网络时，检查路由器是否开启 2.4GHz。</p><p>手机仍连接设备热点时无法访问本网站属于正常情况。完成设备配网后，恢复手机联网再继续；这一步不会解除设备绑定。</p></div></details>
    <section className="stack setup-help" aria-labelledby="setup-screen-help">
      <h3 id="setup-screen-help">看设备屏幕，找到下一步</h3>
      <details className="details"><summary>显示 Wi-Fi 热点名称或配网提示</summary><p>设备还在等待连接家庭网络。在手机 Wi-Fi 设置中连接屏幕提示的设备热点，再打开设备配网页。</p><button className="button secondary" type="button" onClick={() => setStep(1)}>查看连接热点步骤</button></details>
      <details className="details"><summary>一直显示“登录服务器”或连接错误</summary><p>连上 Wi-Fi 不等于已连上服务。先确认家庭网络可以上网；如果刚更换过网络，重新配网。仍然失败时，记录屏幕提示交给维护人员检查服务地址和设备授权，不要反复解绑。</p><button className="button secondary" type="button" onClick={() => setStep(0)}>查看重新配网步骤</button></details>
      <details className="details"><summary>显示绑定二维码或 6 位备用码</summary><p>恢复手机正常联网后，扫描设备二维码，或登录后输入备用码。每台新设备都需要单独认领。</p><Link className="button secondary" href="/claim">去认领设备</Link></details>
      <details className="details"><summary>已经绑定，想换助手或音色</summary><p>先在“我的设备”确认这台设备在线并选择助手，再到“我的助手”配置兼容的音色和语音方案。保存后下一轮生效；阿里托管应用的设置范围以页面提示为准。</p><div className="split"><Link className="button secondary" href="/console/devices">查看我的设备</Link><Link className="button secondary" href="/console/agents">配置我的助手</Link></div></details>
      <details className="details"><summary>显示“本次回复未完成”</summary><p>这表示当前对话未正常完成，不能仅凭这个提示判断扬声器损坏。重新唤醒重试一次；如果重复出现，记录发生时间和当前语音方案，交给维护人员查询日志。</p><Link className="button secondary" href="/console/devices">检查设备在线状态</Link></details>
    </section>
  </div>;
}

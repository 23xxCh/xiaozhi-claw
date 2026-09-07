import Link from "next/link";

export function SetupGuide({ wifiOnly = false }: { wifiOnly?: boolean }) {
  return <div className="stack">
    <h2>{wifiOnly ? "更换设备 Wi-Fi" : "先让设备连上网络"}</h2>
    <p className="muted">{wifiOnly ? "更换网络会暂时离线，助手和账户绑定会保留，不需要解除绑定。" : "使用手机完成下面几步，再扫描设备屏幕上的绑定二维码。"}</p>
    <ol className="stack" style={{ paddingLeft: 24 }}>
      <li><strong>{wifiOnly ? "保持设备通电，进入配网模式。" : "插电，等设备显示配网提示。"}</strong><p className="hint">需要重新配网时，等设备待机后长按 BOOT 约 2 秒。若一直无法进入待机，重新上电，在启动阶段短按 BOOT 进入配网。</p></li>
      <li><strong>在手机的 Wi-Fi 设置中连接 Xiaozhi-XXXX。</strong><p className="hint">这是设备的临时热点。手机提示“无法上网”时选择仍然连接。</p></li>
      <li><strong>打开设备配网页，选择家里的 2.4GHz Wi-Fi。</strong><p className="hint">在设备页面输入密码并保存，等待连接成功。Wi-Fi 密码只交给设备，本网站不收集。</p><a className="button secondary" href="http://192.168.4.1" target="_blank" rel="noopener noreferrer">打开设备配网页</a><p className="hint">没有自动弹出页面时，可在浏览器输入 http://192.168.4.1。</p></li>
      <li><strong>等配网页显示成功，再回到本页。</strong><p className="hint">让手机重新连接家里的 Wi-Fi 或移动网络。热点连接期间请保留本页、不要刷新，也不要提前关闭设备配网页。配网成功还不代表云端在线，返回账户的设备页后再确认在线状态。</p></li>
    </ol>
    {wifiOnly ? <Link className="button" href="/console/devices">返回检查设备在线状态</Link> : <p className="muted">设备联网后会显示绑定二维码；扫码登录即可继续。已有 6 位备用码时，也可以手动输入。</p>}
    <details className="details"><summary>找不到热点或连接失败</summary><div className="stack"><p>确认设备通电，并按上方步骤重新进入配网。看不到家庭网络时，检查路由器是否开启 2.4GHz。</p><p>手机仍连接设备热点时无法访问本网站属于正常情况。完成设备配网后，恢复手机联网再继续；这一步不会解除设备绑定。</p></div></details>
  </div>;
}

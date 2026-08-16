import ConnectClient from "./ConnectClient";

export default async function ConnectPage(props: PageProps<"/connect">) {
  const params = await props.searchParams;
  const connected = typeof params.connected === "string" ? params.connected : null;
  const oauthError = typeof params.error === "string" ? params.error : null;
  return <ConnectClient connected={connected} oauthError={oauthError} />;
}

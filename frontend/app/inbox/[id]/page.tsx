import EmailDetail from "./EmailDetail";

export default async function EmailDetailPage(props: PageProps<"/inbox/[id]">) {
  const { id } = await props.params;
  return <EmailDetail emailId={id} />;
}

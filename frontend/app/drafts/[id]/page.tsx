import DraftDetail from "./DraftDetail";

export default async function DraftDetailPage(props: PageProps<"/drafts/[id]">) {
  const { id } = await props.params;
  return <DraftDetail draftId={id} />;
}

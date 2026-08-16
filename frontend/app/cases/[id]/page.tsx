import CaseDetail from "./CaseDetail";

export default async function CaseDetailPage(props: PageProps<"/cases/[id]">) {
  const { id } = await props.params;
  return <CaseDetail caseId={id} />;
}

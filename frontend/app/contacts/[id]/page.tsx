import ContactDetail from "./ContactDetail";

export default async function ContactDetailPage(props: PageProps<"/contacts/[id]">) {
  const { id } = await props.params;
  return <ContactDetail contactId={id} />;
}

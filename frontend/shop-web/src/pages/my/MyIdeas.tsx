import { PageTitle } from "@/components/shop/bits";
import { IdeaBox } from "@/components/shop/IdeaBox";
import { useAuth } from "@/lib/auth";

export default function MyIdeas() {
  const { user } = useAuth();
  return (
    <>
      <PageTitle title="고객의 소리" />
      {user && <IdeaBox key={user.buyer_id} buyerId={user.buyer_id} />}
    </>
  );
}

import { Navigate, Route, Routes, useLocation, useParams } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ShopShell } from '@/components/shop/ShopShell'
import { AuthProvider } from '@/lib/auth'
import { CartProvider } from '@/lib/cart'
import Cart from '@/pages/Cart'
import Checkout from '@/pages/Checkout'
import Home from '@/pages/Home'
import Legal from '@/pages/Legal'
import DevBuyers from '@/pages/DevBuyers'
import Login from '@/pages/Login'
import OrderDetail from '@/pages/OrderDetail'
import Orders from '@/pages/Orders'
import PaymentFail from '@/pages/PaymentFail'
import PaymentSuccess from '@/pages/PaymentSuccess'
import ProductDetail from '@/pages/ProductDetail'
import Products from '@/pages/Products'
import Signup from '@/pages/Signup'
import Support from '@/pages/Support'
import MyClaims from '@/pages/my/MyClaims'
import MyHome from '@/pages/my/MyHome'
import MyIdeas from '@/pages/my/MyIdeas'
import MyInquiries from '@/pages/my/MyInquiries'
import MyLayout from '@/pages/my/MyLayout'
import MyPassword from '@/pages/my/MyPassword'
import MyProfile from '@/pages/my/MyProfile'
import MyReviews from '@/pages/my/MyReviews'

/** 옛 `/orders/:id` 주소(결제 화면 리다이렉트의 `?placed=1` 포함)를 마이페이지로 옮긴다. */
function OrderRedirect() {
  const { id } = useParams()
  const { search } = useLocation()
  return <Navigate to={`/my/orders/${id}${search}`} replace />
}

export function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <CartProvider>
          <ShopShell>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/products" element={<Products />} />
              <Route path="/products/:id" element={<ProductDetail />} />
              <Route path="/cart" element={<Cart />} />
              <Route path="/checkout" element={<Checkout />} />
              <Route path="/login" element={<Login />} />
              <Route path="/signup" element={<Signup />} />
              <Route path="/dev/buyers" element={<DevBuyers />} />
              <Route path="/ax-orders" element={<Navigate to="/my/orders" replace />} />
              <Route path="/orders" element={<Navigate to="/my/orders" replace />} />
              <Route path="/orders/:id" element={<OrderRedirect />} />
              <Route path="/my" element={<MyLayout />}>
                <Route index element={<MyHome />} />
                <Route path="orders" element={<Orders />} />
                <Route path="orders/:id" element={<OrderDetail />} />
                <Route path="claims" element={<MyClaims />} />
                <Route path="inquiries" element={<MyInquiries />} />
                <Route path="ideas" element={<MyIdeas />} />
                <Route path="reviews" element={<MyReviews />} />
                <Route path="profile" element={<MyProfile />} />
                <Route path="password" element={<MyPassword />} />
              </Route>
              <Route path="/payments/success" element={<PaymentSuccess />} />
              <Route path="/payments/fail" element={<PaymentFail />} />
              <Route path="/support" element={<Support />} />
              <Route path="/terms" element={<Legal kind="terms" />} />
              <Route path="/buyer-terms" element={<Legal kind="buyer-terms" />} />
              <Route path="/privacy" element={<Legal kind="privacy" />} />
              <Route path="*" element={<Home />} />
            </Routes>
          </ShopShell>
        </CartProvider>
      </AuthProvider>
    </ThemeProvider>
  )
}

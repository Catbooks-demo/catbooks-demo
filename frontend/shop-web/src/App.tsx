import { Navigate, Route, Routes } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ShopShell } from '@/components/shop/ShopShell'
import { AuthProvider } from '@/lib/auth'
import { CartProvider } from '@/lib/cart'
import Cart from '@/pages/Cart'
import Checkout from '@/pages/Checkout'
import Home from '@/pages/Home'
import Legal from '@/pages/Legal'
import Login from '@/pages/Login'
import OrderDetail from '@/pages/OrderDetail'
import Orders from '@/pages/Orders'
import PaymentFail from '@/pages/PaymentFail'
import PaymentSuccess from '@/pages/PaymentSuccess'
import ProductDetail from '@/pages/ProductDetail'
import Products from '@/pages/Products'
import Support from '@/pages/Support'

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
              <Route path="/ax-orders" element={<Navigate to="/orders" replace />} />
              <Route path="/orders" element={<Orders />} />
              <Route path="/orders/:id" element={<OrderDetail />} />
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

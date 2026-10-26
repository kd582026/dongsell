import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {title: 'DONGSELL | 셀러 어드민', description: '내 PC에 저장하는 상품 소싱 작업 공간'};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="ko"><body>{children}</body></html>;
}


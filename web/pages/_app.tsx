import type { AppProps } from 'next/app';
import dynamic from 'next/dynamic';
import React from 'react';

import '../nprogress.css';
import '../styles/globals.css';

const MainAppShell = dynamic(() => import('@/app/main-app-shell'), { ssr: false });

export default function MyApp(props: AppProps) {
  return <MainAppShell {...props} />;
}

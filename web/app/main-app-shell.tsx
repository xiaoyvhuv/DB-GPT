import { ChatContext, ChatContextProvider } from '@/app/chat-context';
import i18n from '@/app/i18n';
import SideBar from '@/components/layout/side-bar';
import { NewTaskProvider } from '@/modules/new-task';
import FloatHelper from '@/new-components/layout/FloatHelper';
import { STORAGE_LANG_KEY, STORAGE_USERINFO_KEY, STORAGE_USERINFO_VALID_TIME_KEY } from '@/utils/constants/index';
import { App, ConfigProvider, MappingAlgorithm, theme } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import classNames from 'classnames';
import type { AppProps } from 'next/app';
import Head from 'next/head';
import { useRouter } from 'next/router';
import React, { useContext, useEffect, useState } from 'react';

const antdDarkTheme: MappingAlgorithm = (seedToken, mapToken) => ({
  ...theme.darkAlgorithm(seedToken, mapToken),
  colorBgBase: '#232734',
  colorBorder: '#828282',
  colorBgContainer: '#232734',
});

function CssWrapper({ children }: { children: React.ReactElement }) {
  const { mode } = useContext(ChatContext);

  useEffect(() => {
    if (!mode) return;
    document.body?.classList?.add(mode);
    document.body?.classList?.remove(mode === 'light' ? 'dark' : 'light');
  }, [mode]);

  useEffect(() => {
    i18n.changeLanguage?.(window.localStorage.getItem(STORAGE_LANG_KEY) || 'zh');
  }, [i18n]);

  return <div>{children}</div>;
}

function LayoutWrapper({ children }: { children: React.ReactNode }) {
  const { isMenuExpand, mode } = useContext(ChatContext);
  const [isLogin, setIsLogin] = useState(false);
  const [language, setLanguage] = useState(i18n.language || 'zh');
  const router = useRouter();

  useEffect(() => {
    const updateLanguage = (nextLanguage: string) => setLanguage(nextLanguage);
    i18n.on('languageChanged', updateLanguage);
    return () => i18n.off('languageChanged', updateLanguage);
  }, []);

  useEffect(() => {
    const user = { user_channel: 'dbgpt', user_no: '001', nick_name: 'dbgpt' };
    window.localStorage.setItem(STORAGE_USERINFO_KEY, JSON.stringify(user));
    window.localStorage.setItem(STORAGE_USERINFO_VALID_TIME_KEY, Date.now().toString());
    setIsLogin(true);
  }, []);

  if (!isLogin && !router.pathname.startsWith('/share')) return null;

  const hideSidebar =
    router.pathname.includes('mobile') || router.pathname.startsWith('/share') || !!router.query.from_task;
  const content =
    router.pathname.includes('mobile') || router.pathname.startsWith('/share') ? (
      <>{children}</>
    ) : (
      <div className='flex w-screen h-screen overflow-hidden'>
        <Head>
          <meta name='viewport' content='initial-scale=1.0, width=device-width, maximum-scale=1' />
        </Head>
        {router.pathname !== '/construct/app/extra' && !hideSidebar && (
          <div className={classNames('transition-[width]', isMenuExpand ? 'w-60' : 'w-20', 'hidden', 'md:block')}>
            <SideBar />
          </div>
        )}
        <div className='flex flex-col flex-1 relative overflow-hidden'>{children}</div>
        {!hideSidebar && <FloatHelper />}
      </div>
    );

  return (
    <ConfigProvider
      locale={language === 'en' ? enUS : zhCN}
      theme={{ token: { colorPrimary: '#0C75FC', borderRadius: 4 }, algorithm: mode === 'dark' ? antdDarkTheme : undefined }}
    >
      <App>{content}</App>
    </ConfigProvider>
  );
}

export default function MainAppShell({ Component, pageProps }: AppProps) {
  return (
    <ChatContextProvider>
      <NewTaskProvider>
        <CssWrapper>
          <LayoutWrapper>
            <Component {...pageProps} />
          </LayoutWrapper>
        </CssWrapper>
      </NewTaskProvider>
    </ChatContextProvider>
  );
}

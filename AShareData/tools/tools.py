import datetime as dt

import matplotlib.pyplot as plt
import pandas as pd

from AShareData import AShareDataReader, TradingCalendar, utils
from AShareData.config import get_db_interface
from AShareData.DBInterface import DBInterface
from AShareData.Factor import CompactFactor, ContinuousFactor

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


class MajorIndustryConstitutes(object):
    def __init__(self, provider: str, level: int, cap: CompactFactor = None, db_interface: DBInterface = None):
        self.db_interface = db_interface if db_interface else get_db_interface()
        self.calendar = TradingCalendar(self.db_interface)
        self.date = self.calendar.yesterday()
        self.data_reader = AShareDataReader(self.db_interface)
        self.industry = self.data_reader.industry(provider=provider, level=level)
        self.cap = cap if cap else self.data_reader.stock_free_floating_market_cap

    def get_major_constitute(self, name: str, n: int = None):
        assert name in self.industry.all_industries, 'unknown industry'
        constitute = self.industry.list_constitutes(date=self.date, industry=name)
        sec_name = self.data_reader.sec_name.get_data(ids=constitute, dates=self.date)
        val = self.cap.get_data(ids=constitute, dates=self.date) / 1e8
        ret = pd.concat([sec_name, val], axis=1).sort_values(val.name, ascending=False)
        if n:
            ret = ret.head(n)
        return ret


class IndexHighlighter(object):
    must_keep_indexes = ['全市场.IND', '全市场等权.IND', '次新股等权.IND', 'ST.IND']

    def __init__(self, date: dt.datetime = None, db_interface: DBInterface = None):
        self.db_interface = db_interface if db_interface else get_db_interface()
        self.calendar = TradingCalendar(self.db_interface)
        if date is None:
            date = dt.datetime.combine(dt.date.today(), dt.time())
        self.date = date
        records = utils.load_excel('自编指数配置.xlsx')
        self.tickers = [it['ticker'] for it in records]
        self.tbd_indexes = list(set(self.tickers) - set(self.must_keep_indexes))
        start_date = self.calendar.offset(date, -22)
        index_factor = ContinuousFactor('自合成指数', '收益率', db_interface=self.db_interface)
        self.cache = index_factor.get_data(start_date=start_date, end_date=date).unstack()
        self.industry_cache = []

    def featured_data(self, look_back_period: int, n: int) -> pd.DataFrame:
        data = self.cache.iloc[-look_back_period:, :]
        data = (data + 1).cumprod()
        tmp = data.loc[data.index[-1], self.tbd_indexes].sort_values()
        ordered_index = tmp.index.tolist()
        cols = ordered_index[:n] + ordered_index[-n:]
        self.industry_cache.extend(cols)
        return data.loc[:, cols + self.must_keep_indexes] - 1

    @staticmethod
    def disp_data(data):
        print(data.loc[data.index[-1], :].T.sort_values(ascending=False) * 100)

    def plot_index(self, period: int, n: int, ax: plt.Axes = None):
        plot_data = self.featured_data(period, n)
        if ax is None:
            _, ax = plt.subplots(1, 1)
        plot_data.plot(ax=ax)
        ax.set_xlim(left=plot_data.index[0], right=plot_data.index[-1])
        ax.grid(True)
        return ax

    def summary(self):
        for i, it in enumerate([(3, 3), (5, 3), (20, 3)]):
            print(f'回溯{it[0]}天:')
            self.disp_data(self.featured_data(it[0], it[1]))
            print('')
        self.plot_index(20, 3)
        mentioned_industry = [it[2:-4] for it in set(self.industry_cache) if it.startswith('申万')]
        constitute = MajorIndustryConstitutes(provider='申万', level=2)
        for it in mentioned_industry:
            print(f'申万2级行业 - {it}')
            print(constitute.get_major_constitute(it, 10))
            print('')
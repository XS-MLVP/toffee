搭建验证环境
================

**toffee和toffee-test** 提供了搭建验证环境全流程所需要的方法和工具，本章中将详细介绍如何使用 **toffee和toffee-test** 搭建一个完整的验证环境。

在阅读前请确保您已经阅读了 [如何编写规范的验证环境](/docs/mlvp/canonical_env)，并了解了 toffee 规范验证环境的基本结构。

对于一次全新的验证工作来说，按照环境搭建步骤的开始顺序，搭建验证环境可以分为以下几个步骤：

1. 按照逻辑功能划分 DUT 接口，并定义 Bundle
2. 为每个 Bundle 编写 Agent，完成对 Bundle 的高层封装
3. 将多个 Agent 封装成 Env，完成对整个 DUT 的高层封装
4. 按照 Env 的接口规范编写参考模型，并将其与 Env 进行绑定

本章将会分别介绍每个步骤中如何使用 toffee和toffee-test 中的工具来完成环境搭建需求。


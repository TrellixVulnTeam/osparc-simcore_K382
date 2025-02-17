/* ************************************************************************

   osparc - the simcore frontend

   https://osparc.io

   Copyright:
     2018 IT'IS Foundation, https://itis.swiss

   License:
     MIT: https://opensource.org/licenses/MIT

   Authors:
     * Pedro Crespo (pcrespov)

************************************************************************ */

/** Application's preferences
 *
 *  - multi-page modal window
 *
*/

qx.Class.define("osparc.desktop.preferences.PreferencesWindow", {
  extend: osparc.ui.window.SingletonWindow,

  construct: function() {
    this.base(arguments, "preferences", this.tr("Preferences"));
    this.set({
      layout: new qx.ui.layout.Grow(),
      modal: true,
      width: 550,
      height: 550 * 1.2,
      showClose: true,
      showMaximize: false,
      showMinimize: false,
      resizable: false,
      appearance: "service-window"
    });
    const closeBtn = this.getChildControl("close-button");
    osparc.utils.Utils.setIdToWidget(closeBtn, "preferencesWindowCloseBtn");

    const tabView = new qx.ui.tabview.TabView().set({
      barPosition: "left",
      contentPadding: 0
    });

    const profPage = new osparc.desktop.preferences.pages.ProfilePage();
    const profBtn = profPage.getChildControl("button");
    osparc.utils.Utils.setIdToWidget(profBtn, "preferencesProfileTabBtn");
    tabView.add(profPage);

    const secPage = new osparc.desktop.preferences.pages.SecurityPage();
    const secBtn = secPage.getChildControl("button");
    osparc.utils.Utils.setIdToWidget(secBtn, "preferencesSecurityTabBtn");
    tabView.add(secPage);

    const tokensPage = new osparc.desktop.preferences.pages.TokensPage();
    const tokensBtn = tokensPage.getChildControl("button");
    osparc.utils.Utils.setIdToWidget(tokensBtn, "preferencesTokensTabBtn");
    tabView.add(tokensPage);

    const expPage = new osparc.desktop.preferences.pages.ExperimentalPage();
    const expBtn = expPage.getChildControl("button");
    osparc.utils.Utils.setIdToWidget(expBtn, "preferencesExperimentalTabBtn");
    tabView.add(expPage);

    const confirmPage = new osparc.desktop.preferences.pages.ConfirmationsPage();
    tabView.add(confirmPage);

    if (osparc.data.Permissions.getInstance().canDo("user.tag")) {
      const tagsPage = new osparc.desktop.preferences.pages.TagsPage();
      osparc.utils.Utils.setIdToWidget(tagsPage.getChildControl("button"), "preferencesTagsTabBtn");
      tabView.add(tagsPage);
    }

    const orgsPage = new osparc.desktop.preferences.pages.OrganizationsPage();
    const orgsBtn = orgsPage.getChildControl("button");
    osparc.utils.Utils.setIdToWidget(orgsBtn, "preferencesOrganizationsTabBtn");
    tabView.add(orgsPage);
    orgsBtn.exclude();
    osparc.data.Resources.get("organizations")
      .then(resp => {
        const orgs = resp["organizations"];
        if (orgs.length || osparc.data.Permissions.getInstance().canDo("user.organizations.create")) {
          orgsBtn.show();
        }
      });

    const clustersPage = new osparc.desktop.preferences.pages.ClustersPage();
    const clustersBtn = clustersPage.getChildControl("button");
    osparc.utils.Utils.setIdToWidget(clustersBtn, "preferencesClustersTabBtn");
    tabView.add(clustersPage);
    clustersBtn.exclude();
    osparc.utils.DisabledPlugins.isClustersDisabled()
      .then(isDisabled => {
        if (isDisabled === false) {
          osparc.data.Resources.get("clusters")
            .then(clusters => {
              if (clusters.length || osparc.data.Permissions.getInstance().canDo("user.clusters.create")) {
                clustersBtn.show();
              }
            });
        }
      });

    if (osparc.data.Permissions.getInstance().canDo("statics.read")) {
      const testerPage = new osparc.desktop.preferences.pages.TesterPage();
      tabView.add(testerPage);
    }

    this.add(tabView);
  }
});

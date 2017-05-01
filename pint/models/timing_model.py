# timing_model.py
# Defines the basic timing model interface classes
import functools
from .parameter import Parameter, strParameter
from ..phase import Phase
from astropy import log
import astropy.time as time
import numpy as np
import pint.utils as utils
import astropy.units as u
from astropy.table import Table
import copy
import abc
import six
from collections import OrderedDict
import inspect

# parameters or lines in parfiles to ignore (for now?), or at
# least not to complain about
ignore_params = ['START', 'FINISH', 'SOLARN0', 'EPHEM', 'CLK', 'UNITS',
                 'TIMEEPH', 'T2CMETHOD', 'CORRECT_TROPOSPHERE', 'DILATEFREQ',
                 'NTOA', 'CLOCK', 'TRES', 'TZRMJD', 'TZRFRQ', 'TZRSITE',
                 'NITS', 'IBOOT','BINARY']
ignore_prefix = ['DMXF1_','DMXF2_','DMXEP_'] # DMXEP_ for now.

class Cache(object):
    """Temporarily cache timing model internal computation results.

    The Cache class defines two decorators, use_cache and cache_result.
    """

    # The name of the cache attribute
    the_cache = "cache"

    @classmethod
    def cache_result(cls, function):
        """Caching decorator for functions.

        This can be applied as a decorator to any timing model method
        for which it might be useful to store the value, once computed
        for a given TOA.  Note that the cache must be manually enabled
        and cleared when appropriate, so this functionality should be
        used with care.
        """
        the_func = function.__name__
        @functools.wraps(function)
        def get_cached_result(*args, **kwargs):
            log.debug("Checking for cached value of %s" % the_func)
            # What to do about checking for a change of arguments?
            # args[0] should be a "self"
            if hasattr(args[0], cls.the_cache):
                cache = getattr(args[0], cls.the_cache)
                if isinstance(cache, cls):
                    if hasattr(cache, the_func):
                        # Return the cached value
                        log.debug(" ... using cached result")
                        return getattr(cache, the_func)
                    else:
                        # Evaluate the function and cache the results
                        log.debug(" ... computing new result")
                        result = function(*args, **kwargs)
                        setattr(cache, the_func, result)
                        return result
            # Couldn't access the cache, just return the result
            # without caching it.
            log.debug(" ... no cache found")
            return function(*args, **kwargs)
        return get_cached_result

    @classmethod
    def use_cache(cls, function):
        """Caching decorator for functions.

        This can be applied as a decorator to a function that should
        internally use caching of function return values.  The cache
        will be deleted when the function exits.  If the top-level function
        calls other functions that have caching enabled they will share
        the cache, and it will only be deleted when the top-level function
        exits.
        """
        @functools.wraps(function)
        def use_cached_results(*args, **kwargs):
            # args[0] should be a "self"
            # Test whether a cache attribute is present
            if hasattr(args[0], cls.the_cache):
                cache = getattr(args[0], cls.the_cache)
                # Test whether caching is already enabled
                if isinstance(cache, cls):
                    # Yes, just execute the function
                    return function(*args, **kwargs)
                else:
                    # Init the cache, excute the function, then delete cache
                    setattr(args[0], cls.the_cache, cls())
                    result = function(*args, **kwargs)
                    setattr(args[0], cls.the_cache, None)
                    return result
            else:
                # no "self.cache" attrib is found.  Could raise an error, or
                # just execute the function normally.
                return function(*args, **kwargs)
        return use_cached_results


class TimingModel(object):
    """
    Base-level object provides an interface for implementing pulsar timing
    models. It contains several over all wrapper methods.

    Notes
    -----
    PINT models pulsar pulse time of arrival at observer from its emission process and
    propagation to observer. Emission generally modeled as pulse 'Phase' and propagation.
    'time delay'. In pulsar timing different astrophysics phenomenons are separated to
    time model components for handling a specific emission or propagation effect.

    All timing model component classes should subclass this timing model base class.
    Each timing model component generally requires the following parts:
        Timing Parameters
        Delay/Phase functions which implements the time delay and phase.
        Derivatives of delay and phase respect to parameter for fitting toas.
    Each timing parameters are stored as TimingModel attribute in the type of `pint.model.parameter`
    delay or phase and its derivatives are implemented as TimingModel Methods.

    Attributes
    ----------
    params : list
        A list of all the parameter names.
    prefix_params : list
        A list of prefixed parameter names.
    delay_funcs : dict
        All the delay functions implemented in timing model. The delays do not
        need barycentric toas are placed under the 'L1' keys as a list of methods,
        the ones needs barycentric toas are under the 'L2' delay. This will be improved
        in the future. One a delay method is defined in model component, it should
        get registered in this dictionary.
    phase_funcs : list
        All the phase functions implemented in timing model. Once a phase method is defined
        in model component, it should get registered in this list.
    delay_derivs : list
        All the delay derivatives respect to timing parameters.
        Once a delay derivative method is defined in model component, it should get registered in this list.
    phase_derivs : list
        All the phase derivatives respect to timing parameters.
        Once a phase derivative method is defined in model component, it should get registered in this list.
    phase_derivs_wrt_delay : list
        All the phase derivatives respect to delay.
    """

    def __init__(self, name='', components=[]):
        self.name = name
        self.component_types = ['DelayComponent', 'PhaseComponent']
        self.setup_component_dict()
        self.top_level_params = []
        self.add_param_from_top(strParameter(name="PSR",
            description="Source name",
            aliases=["PSRJ", "PSRB"]), '')

        for cp in components:
            self.add_component(cp)

    def setup(self):
        """This is a abstract class for setting up timing model class. It is designed for
        reading .par file and check parameters.
        """
        for cp in list(self.components.values()):
            cp.setup()

    def setup_component_dict(self):
        """
        An OrderedDict will be create for all the component types listed in the
        attribute component_types. The name template will be 'type name'+'_dict'.
        """
        for ct in self.component_types:
            if hasattr(self, ct+'_dict'):
                continue
            else:
                setattr(self, ct+'_dict', OrderedDict())

    def __str__(self):
        result = ""
        comps = self.components
        for k, cp in list(comps.items()):
            result += "In component '%s'" % k + "\n\n"
            for pp in cp.params:
                result += str(getattr(cp, pp)) + "\n"
        return result

    def __getattr__(self, name):
        try:
            if six.PY2:
                return super(TimingModel, self).__getattribute__(name)
            else:
                return super().__getattribute__(name)
        except AttributeError:
            if six.PY2:
                cp = super(TimingModel, self).__getattribute__('search_cmp_attr')(name)
                if cp is not None:
                    return super(cp.__class__, cp).__getattribute__(name)
                else:
                    raise AttributeError("'%s' object has no attribute '%s'." %
                                         (self.__class__.__name__, name))
            else:
                cp = super().__getattribute__('search_cmp_attr')(name)
                if cp is not None:
                    return cp.__getattribute__(name)
                else:
                    raise AttributeError("'%s' object has no attribute '%s'." %
                                         (self.__class__.__name__, name))

    @property
    def params(self,):
        p = self.top_level_params
        for cp in list(self.components.values()):
            p = p+cp.params
        return p

    @property
    def components(self,):
        """This will return a dictionary of all the components
        """
        comps = {}
        for ct in self.component_types:
            cps = list(getattr(self, ct+'_dict').values())
            for cp in cps:
                comps[cp.__class__.__name__] = cp
        return comps

    @property
    def delay_funcs(self,):
        dfs = []
        for d in list(self.DelayComponent_dict.values()):
            dfs += d.delay_funcs_component
        return dfs

    @property
    def phase_funcs(self,):
        pfs = []
        for p in list(self.PhaseComponent_dict.values()):
            pfs += p.phase_funcs_component
        return pfs

    @property
    def phase_deriv_funcs(self):
        return self.get_deriv_funcs('PhaseComponent')

    @property
    def delay_deriv_funcs(self):
        return self.get_deriv_funcs('DelayComponent')

    @property
    def d_phase_d_delay_funcs(self):
        phase_comps = list(self.PhaseComponent_dict.values())
        Dphase_Ddelay = []
        for cp in phase_comps:
            Dphase_Ddelay += cp.phase_derivs_wrt_delay
        return Dphase_Ddelay

    def get_deriv_funcs(self, component_type):
        componet_dict = component_type + '_dict'
        type_components = list(getattr(self, componet_dict).values())
        deriv_funcs = {}
        for cp in type_components:
            for k, v in list(cp.deriv_funcs.items()):
                if k in deriv_funcs:
                    deriv_funcs[k] += v
                else:
                    deriv_funcs[k] = v
        return deriv_funcs

    def search_cmp_attr(self, name):
        """
        This is a function for searching an attribute from all the components.
        If the multiple components has same attribute, it will return the first
        component.
        """
        cmp = None
        for cp in list(self.components.values()):
            try:
                _ = super(cp.__class__, cp).__getattribute__(name)
                cmp = cp
                break
            except AttributeError:
                continue
        return cmp

    def add_component(self, component, order=None, force=False):
        """
        This is a method to add a component to the timing model
        Parameter
        ---------
        component: component instance
            The component need to be added to the timing model
        order: int, optional
            The order of component
        force: bool, optional
            If add a duplicated type of component
        """
        # check component type
        comp_base = inspect.getmro(component.__class__)
        # NOTE Since a component can be inhertance from other component
        # We inspect all the component bases.
        # inspect getmro method returns the base classes (including cls)
        # in method resolution order. The third level of inhertance class name
        # is what we want. Object --> component --> TypeComponent.
        # (ie DelayComponent)
        # This class type is in the third to the last of the getmro returned
        # result.
        comp_type = comp_base[-3].__name__
        if comp_type in self.component_types:
            comp_type_dict = getattr(self, comp_type+'_dict')
        else:
            self.component_types.append(comp_type)
            self.setup_component_dict()
            comp_type_dict = getattr(self, comp_type+'_dict')

        orders = list(comp_type_dict.keys())
        comps = list(comp_type_dict.values())
        comp_classes = [x.__class__ for x in comps]
        if component.__class__ in comp_classes:
            log.warn("Component '%s' is already added." %
                     component.__class__.__name__)
            if not force:
                log.warn("Component '%s' will not be added. To force add it, use"
                         " force option." % component.__class__.__name__)
                return
        component._parent = self
        if order is None:
            if len(orders) > 0:
                order = orders[-1] + 1
            else:
                order = 1
            comp_type_dict.update({order: component})
        else:
            component_items = comp_type_dict.items()
            if order in orders: # PUSH the order back
                idx = component_items.index((order, comp_type_dict[order]))
                for ii in range(idx, len(component_items)):
                    component_items[ii][0] += 1
            component_items.append((order, component))
            setattr(self, comp_type+'_dict', OrderedDict(sorted(component_items,\
                    key=lambda t: t[0])))

    def remove_component(self, component):
        comp, old_order, comp_type_dict, comp_type = \
               self.get_component_instance(component)
        comp_items = list(comp_type_dict.items())
        remove_comp = (old_order, comp)
        comp_items.remove(remove_comp)
        setattr(self, comp_type+'_dict', OrderedDict(sorted(comp_items, \
                key=lambda t: t[0])))

    def change_component_order(self, component, new_order, switch_order=False):
        cmp, old_order, comp_host, comp_type = \
               self.get_component_instance(component)
        comp_items = list(comp_host.items())
        orders = np.array(list(comp_host.keys()))
        idx = comp_items.index((old_order, cmp))
        if new_order not in orders:
            comp_items.append((new_order, cmp))
            comp_items.remove((old_order, cmp))
            setattr(self, comp_type+'_dict', OrderedDict(sorted(comp_items, \
                   key=lambda t: t[0])))

        else:
            affected_comp = (new_order, comp_host[new_order])
            if switch_order:
                idx_affected = comp_items.index(affected_comp)
                switched1 = (new_order, cmp)
                switched2 = (old_order, comp_host[new_order])
                comp_items.remove(affected_comp)
                comp_items.append(switched1)
                comp_items.remove((old_order, cmp))
                comp_items.append(switched2)
                setattr(self, comp_type+'_dict', OrderedDict(sorted(comp_items,\
                       key=lambda t: t[0])))
            else:
                if new_order > old_order:
                    order_effected_index = np.logical_and(orders > old_order, \
                                                          orders <= new_order)
                    factor = -1

                else:
                    order_effected_index = np.logical_and(orders < old_order, \
                                                          orders >= new_order)
                    factor = 1
                order_effected = orders[order_effected_index]
                new_comp_item = []
                for o, c in comp_items:
                    if o == old_order:
                        new_comp_item.append((new_order, cmp))
                        continue
                    if o in order_effected:
                        new_comp_item.append((o+factor, c))
                        continue
                    new_comp_item.append((o, c))
                setattr(self, comp_type+'_dict', OrderedDict(sorted(new_comp_item,\
                       key=lambda t: t[0])))

    def get_component_instance(self, component):
        comps = self.components
        if isinstance(component, str):
            if component not in list(comps.keys()):
                raise AttributeError("No '%s' in the timing model." % component)
            comp = comps[component]
        else: # When component is an component instance.
            if component not in list(comps.values()):
                raise AttributeError("No '%s' in the timing model." \
                                     % component.__class__.__name__)
            else:
                comp = component
        comp_base = inspect.getmro(comp.__class__)
        comp_type = comp_base[-3].__name__
        comp_type_dict = getattr(self, comp_type+'_dict')
        for k,v in list(comp_type_dict.items()):
            if v == comp:
                order = k
            else:
                continue
        return comp, order, comp_type_dict, comp_type

    def get_component_of_category(self):
        category = {}
        for cp in list(self.components.values()):
            cat = cp.category
            if cat in list(category.keys()):
                category[cat].append(cp)
            else:
                category[cat] = [cp,]
        return category

    def add_param_from_top(self, param, target_component):
        """Add a parameter to a timing model component.
        """
        if target_component == '':
            setattr(self, param.name, param)
            self.top_level_params += [param.name]
        else:
            if target_component not in list(self.components.keys()):
                raise AttributeError("Can not find component '%s' in "
                                     "timging model." % target_component)
            self.components[target_component].add_param(param)

    def remove_param(self, param):
        """
        Remove a parameter from timing model
        Parameter
        ---------
        param: str
            The name of parameter need to be removed.
        """
        param_map = self.get_params_mapping()
        if param not in list(param_map.keys()):
            raise AttributeError("Can not find '%s' in timing model." % param.name)
        if param_map[param] == 'timing_model':
            delattr(self, param)
            self.top_level_params.remove(param)
        else:
            target_component = param_map[param]
            self.components[target_component].remove_param(param)

    def get_params_mapping(self):
        """
        This is a method to map all the parameters to the component they belong
        to.
        """
        param_mapping = {}
        for p in self.top_level_params:
            param_mapping[p] = 'timing_model'
        for cp in list(self.components.values()):
            for pp in cp.params:
                param_mapping[pp] = cp.__class__.__name__
        return param_mapping

    def get_params_of_type(self, param_type):
        """ Get all the parameters in timing model for one specific type
        """
        result = []
        for p in self.params:
            par = getattr(self, p)
            par_type = type(par).__name__
            par_prefix = par_type[:-9]
            if param_type.upper() == par_type.upper() or \
                param_type.upper() == par_prefix.upper():
                result.append(par.name)
        return result
    def get_prefix_mapping(self,prefix):
        """Get the index mapping for the prefix parameters.
           Parameter
           ----------
           prefix : str
               Name of prefix.
           Return
           ----------
           A dictionary with prefix pararameter real index as key and parameter
           name as value.
        """
        parnames = [x for x in self.params if x.startswith(prefix)]
        mapping = dict()
        for parname in parnames:
            par = getattr(self, parname)
            if par.is_prefix == True and par.prefix == prefix:
                mapping[par.index] = parname
        return mapping

    def param_help(self):
        """Print help lines for all available parameters in model.
        """
        s = "Available parameters for %s\n" % self.__class__
        for par, cp in list(self.get_params_mapping().items()) :
            s += "%s\nLocated in component '%s'\n" % \
                 (getattr(self, par).help_line(), cp)
        return s

    def delay(self, toas):
        """Total delay for the TOAs.

        Return the total delay which will be subtracted from the given
        TOA to get time of emission at the pulsar.
        """
        delay = np.zeros(len(toas))
        for df in self.delay_funcs:
                delay += df(toas, delay)
        return delay

    def phase(self, toas):
        """Return the model-predicted pulse phase for the given TOAs."""
        # First compute the delays to "pulsar time"
        delay = self.delay(toas)
        phase = Phase(np.zeros(len(toas)), np.zeros(len(toas)))
        # Then compute the relevant pulse phases
        for pf in self.phase_funcs:
            phase += Phase(pf(toas, delay))
        return phase

    def get_barycentric_correction(self, toas, last_component_order=None):
        """
        This is a function to calculate the timing delays for correcting TOAs
        to solar system barycenter.
        Parameter
        ---------
        toas: TOAs table
        last_component_order: int, optional
            The order number of the last component to be included in the
            calculation. If it is not provided, it will assign the one before
            the binary model component.
        """
        delay = np.zeros(len(toas))
        end_order = 0
        delay_list = list(self.DelayComponent_dict.items())
        if last_component_order is None:
            # search for binary model.
            for ii, c in enumerate(delay_list):
                if c[1].category == 'binary':
                   end_order = delay_list[ii - 1][0]
                   break
            if end_order == 0:
                end_order = delay_list[-1][0]
        else:
            end_order = last_component_order
        for cp in delay_list:
            if cp[0] > end_order:
                break
            for df in cp[1].delay_funcs_component:
                delay += df(toas, delay)
        return delay

    def get_barycentric_toas(self, toas, last_component_order=None):
        corr = self.get_barycentric_correction(toas, last_component_order)
        return toas['tdbld'] * u.day - corr * u.second

    def d_phase_d_toa(self, toas, sample_step=None):
        """Return the derivative of phase wrt TOA
        Parameter
        ---------
        toas : PINT TOAs class
            The toas when the derivative of phase will be evaluated at.
        sample_step : float optional
            Finite difference steps. If not specified, it will take 1/10 of the
            spin period.
        """
        copy_toas = copy.deepcopy(toas)
        if sample_step is None:
            pulse_period = 1.0 / self.F0.value
            sample_step = pulse_period * 1000
        sample_dt = [-sample_step, 2 * sample_step]

        sample_phase = []
        for dt in sample_dt:
            dt_array = ([dt] * copy_toas.ntoas) * u.s
            deltaT = time.TimeDelta(dt_array)
            copy_toas.adjust_TOAs(deltaT)
            phase = self.phase(copy_toas.table)
            sample_phase.append(phase)
        # Use finite difference method.
        # phase'(t) = (phase(t+h)-phase(t-h))/2+ 1/6*F2*h^2 + ..
        # The error should be near 1/6*F2*h^2
        dp = (sample_phase[1] - sample_phase[0])
        d_phase_d_toa = dp.int / (2*sample_step) + dp.frac / (2*sample_step)
        del copy_toas
        return d_phase_d_toa

    def d_phase_d_tpulsar(self, toas):
        """Return the derivative of phase wrt time at the pulsar.

        NOT implemented yet.
        """
        pass

    def d_phase_d_param(self, toas, delay, param):
        """ Return the derivative of phase with respect to the parameter.
        """
        # TODO need to do correct chain rule stuff wrt delay derivs, etc
        # Is it safe to assume that any param affecting delay only affects
        # phase indirectly (and vice-versa)??
        par = getattr(self, param)
        result = np.longdouble(np.zeros(len(toas))) * u.Unit('')/par.units
        param_phase_derivs = []
        phase_derivs = self.phase_deriv_funcs
        delay_derivs = self.delay_deriv_funcs
        if param in list(phase_derivs.keys()):
            for df in phase_derivs[param]:
                result += df(toas, param, delay).to(result.unit,
                            equivalencies=u.dimensionless_angles())
        else:
            # Apply chain rule for the parameters in the delay.
            # total_phase = Phase1(delay(param)) + Phase2(delay(param))
            # d_total_phase_d_param = d_Phase1/d_delay*d_delay/d_param +
            #                         d_Phase2/d_delay*d_delay/d_param
            #                       = (d_Phase1/d_delay + d_Phase2/d_delay) *
            #                         d_delay_d_param

            d_delay_d_p = self.d_delay_d_param(toas, param)
            dpdd_result = np.longdouble(np.zeros(len(toas))) * u.Unit('')/u.second
            for dpddf in self.d_phase_d_delay_funcs:
                dpdd_result += dpddf(toas, delay)
            result = dpdd_result * d_delay_d_p
        return result.to(result.unit, equivalencies=u.dimensionless_angles())

    def d_delay_d_param(self, toas, param, acc_delay=None):
        """
        Return the derivative of delay with respect to the parameter.
        """
        par = getattr(self, param)
        result = np.longdouble(np.zeros(len(toas)) * u.s/par.units)
        delay_derivs = self.delay_deriv_funcs
        if param not in list(delay_derivs.keys()):
            raise AttributeError("Derivative function for '%s' is not provided"
                                 " or not registred. "%param)
        for df in delay_derivs[param]:
            result += df(toas, param, acc_delay).to(result.unit, \
                        equivalencies=u.dimensionless_angles())
        return result

    def d_phase_d_param_num(self, toas, param, step=1e-2):
        """ Return the derivative of phase with respect to the parameter.
        """
        # TODO : We need to know the range of parameter.
        par = getattr(self, param)
        ori_value = par.value
        unit = par.units
        if ori_value == 0:
            h = 1.0 * step
        else:
            h = ori_value * step
        parv = [par.value-h, par.value+h]

        phaseI = np.zeros((len(toas),2))
        phaseF = np.zeros((len(toas),2))
        for ii, val in enumerate(parv):
            par.value = val
            ph = self.phase(toas)
            phaseI[:,ii] = ph.int
            phaseF[:,ii] = ph.frac
        resI = (- phaseI[:,0] + phaseI[:,1])
        resF = (- phaseF[:,0] + phaseF[:,1])
        result = (resI + resF)/(2.0 * h)
        # shift value back to the original value
        par.value = ori_value
        return result * u.Unit("")/unit

    def d_delay_d_param_num(self, toas, param, step=1e-2):
        """ Return the derivative of phase with respect to the parameter.
        """
        # TODO : We need to know the range of parameter.
        par = getattr(self, param)
        ori_value = par.value
        if ori_value is None:
             # A parameter did not get to use in the model
            log.warn("Parameter '%s' is not used by timing model." % param)
            return np.zeros(len(toas)) * (u.second/par.units)
        unit = par.units
        if ori_value == 0:
            h = 1.0 * step
        else:
            h = ori_value * step
        parv = [par.value-h, par.value+h]
        delay = np.zeros((len(toas),2))
        for ii, val in enumerate(parv):
            par.value = val
            try:
                delay[:,ii] = self.delay(toas)
            except:
                par.value = ori_value
                raise
        d_delay = (-delay[:,0] + delay[:,1])/2.0/h
        par.value = ori_value
        return d_delay * (u.second/unit)

    def designmatrix(self, toas,acc_delay=None, scale_by_F0=True, \
                     incfrozen=False, incoffset=True):
        """
        Return the design matrix: the matrix with columns of d_phase_d_param/F0
        or d_toa_d_param
        """
        params = ['Offset',] if incoffset else []
        params += [par for par in self.params if incfrozen or
                not getattr(self, par).frozen]

        F0 = self.F0.quantity        # 1/sec
        ntoas = len(toas)
        nparams = len(params)
        delay = self.delay(toas)
        units = []

        # Apply all delays ?
        #tt = toas['tdbld']
        #for df in self.delay_funcs:
        #    tt -= df(toas)

        M = np.zeros((ntoas, nparams))
        for ii, param in enumerate(params):
            if param == 'Offset':
                M[:,ii] = 1.0
                units.append(u.s/u.s)
            else:
                # NOTE Here we have negative sign here. Since in pulsar timing
                # the residuals are calculated as (Phase - int(Phase)), which is different
                # from the conventional defination of least square definetion (Data - model)
                # We decide to add minus sign here in the design matrix, so the fitter
                # keeps the conventional way.
                q = - self.d_phase_d_param(toas, delay,param)
                M[:,ii] = q
                units.append(u.Unit("")/ getattr(self, param).units)

        if scale_by_F0:
            mask = []
            for ii, un in enumerate(units):
                if params[ii] == 'Offset':
                    continue
                units[ii] = un * u.second
                mask.append(ii)
            M[:, mask] /= F0.value
        return M, params, units, scale_by_F0

    def read_parfile(self, filename):
        """Read values from the specified parfile into the model parameters."""
        checked_param = []
        repeat_param = {}
        param_map = self.get_params_mapping()
        comps = self.components
        pfile = open(filename, 'r')
        for l in [pl.strip() for pl in pfile.readlines()]:
            # Skip blank lines
            if not l:
                continue
            # Skip commented lines
            if l.startswith('#') or l[:2]=="C ":
                continue

            k = l.split()
            name = k[0].upper()

            if name in checked_param:
                if name in repeat_param.keys():
                    repeat_param[name] += 1
                else:
                    repeat_param[name] = 2
                k[0] = k[0] + str(repeat_param[name])
                l = ' '.join(k)

            parsed = False
            for par in param_map.keys():
                host_comp = param_map[par]
                if host_comp != 'timing_model':
                    cmp = comps[host_comp]
                else:
                    cmp = self
                if cmp.__getattr__(par).from_parfile_line(l):
                    parsed = True
            if not parsed:
                try:
                    prefix,f,v = utils.split_prefixed_name(l.split()[0])
                    if prefix not in ignore_prefix:
                        log.warn("Unrecognized parfile line '%s'" % l)
                except:
                    if l.split()[0] not in ignore_params:
                        log.warn("Unrecognized parfile line '%s'" % l)

            checked_param.append(name)
        # The "setup" functions contain tests for required parameters or
        # combinations of parameters, etc, that can only be done
        # after the entire parfile is read
        self.setup()

    def as_parfile(self, start_order=['astrometry', 'spindown', 'dispersion'],
                         last_order=['jump_delay']):
        """Returns a parfile representation of the entire model as a string."""
        result_begin = ""
        result_end = ""
        result_middle = ""
        cates_comp = self.get_component_of_category()
        printed_cate = []
        for p in self.top_level_params:
            result_begin += getattr(self, p).as_parfile_line()
        for cat in start_order:
            if cat in list(cates_comp.keys()):
                cp = cates_comp[cat]
                for cpp in cp:
                    result_begin += cpp.print_par()
                printed_cate.append(cat)
            else:
                continue

        for cat in last_order:
            if cat in list(cates_comp.keys()):
                cp = cates_comp[cat]
                for cpp in cp:
                    result_end += cpp.print_par()
                printed_cate.append(cat)
            else:
                continue

        for c in list(cates_comp.keys()):
            if c in printed_cate:
                continue
            else:
                cp = cates_comp[c]
                for cpp in cp:
                    result_middle += cpp.print_par()
                printed_cate.append(cat)

        return result_begin + result_middle + result_end

    #

    #

    #
    # #@Cache.use_cache
    #
    #

    #





    #

    # def print_param_control(self, control_info={'UNITS': 'TDB', 'TIMEEPH':'FB90'},
    #                       order=['UNITS', 'TIMEEPH']):
    #     result = ""
    #     for pc in order:
    #         if pc not in control_info.keys():
    #             continue
    #         result += pc + ' ' + control_info[pc] + '\n'
    #     return result
    #
    # def print_param_component(self, component_name):
    #     result = ''
    #     if component_name not in self.components:
    #         return result
    #     else:
    #         if hasattr(self, self.components[component_name].param_print_func):
    #             result += getattr(self, self.components[component_name].param_print_func)()
    #         else:
    #             for p in self.components[component_name].params:
    #                 par = getattr(self, p)
    #                 if par.quantity is not None:
    #                     result += par.as_parfile_line()
    #     return result
    #
    # def as_parfile(self):
    #     """Returns a parfile representation of the entire model as a string."""
    #     result = ""
    #     result += self.PSR.as_parfile_line()
    #     sort_comps = self.sort_model_components()
    #     for scp in sort_comps:
    #         result += self.print_param_component(scp)
    #     result += self.print_param_control()
    #     return result

class ModelMeta(abc.ABCMeta):
    """
    This is a Meta class for timing model registeration. In order ot get a
    timing model registered, a member called 'register' has to be set true in the
    TimingModel subclass.
    """
    def __init__(cls, name, bases, dct):
        regname = '_component_list'
        if not hasattr(cls,regname):
            setattr(cls,regname,{})
        if 'register' in dct:
            if cls.register:
                getattr(cls,regname)[name] = cls
        super(ModelMeta, cls).__init__(name, bases, dct)


@six.add_metaclass(ModelMeta)
class Component(object):
    """ This is a base class for timing model components.
    """
    def __init__(self,):
        self.params = []
        self._parent = None
        self.category = ''
        self.deriv_funcs = {}
        self.component_special_params = []
    def setup(self,):
        pass

    def __getattr__(self, name):
        try:
            if six.PY2:
                return super(Component, self).__getattribute__(name)
            else:
                return super().__getattribute__(name)
        except AttributeError:
            if self._parent is None:
                raise AttributeError("'%s' object has no attribute '%s'." %
                                     (self.__class__.__name__, name))
            else:
                return self._parent.__getattr__(name)

    def add_param(self, param):
        """
        Add a parameter into the Component
        Parameter
        ---------
        param: str
            The name of parameter need to be add.
        """
        setattr(self, param.name, param)
        self.params += [param.name,]

    def remove_param(self, param):
        self.params.remove(param)
        par = getattr(self, param)
        all_names = [param, ] + par.aliases
        if param in self.component_special_params:
            for pn in all_names:
                self.component_special_params.remove(pn)
        delattr(self, param)


    def set_special_params(self, spcl_params):
        als = []
        for p in spcl_params:
            als += getattr(self, p).aliases
        spcl_params += als
        for sp in spcl_params:
            if sp not in self.component_special_params:
                self.component_special_params.append(sp)

    def param_help(self):
        """Print help lines for all available parameters in model.
        """
        s = "Available parameters for %s\n" % self.__class__
        for par in self.params:
            s += "%s\n" % getattr(self, par).help_line()
        return s

    def get_params_of_type(self, param_type):
        """ Get all the parameters in timing model for one specific type
        """
        result = []
        for p in self.params:
            par = getattr(self, p)
            par_type = type(par).__name__
            par_prefix = par_type[:-9]
            if param_type.upper() == par_type.upper() or \
                param_type.upper() == par_prefix.upper():
                result.append(par.name)
        return result

    #@Cache.use_cache
    def get_prefix_mapping_component(self,prefix):
        """Get the index mapping for the prefix parameters.
           Parameter
           ----------
           prefix : str
               Name of prefix.
           Return
           ----------
           A dictionary with prefix pararameter real index as key and parameter
           name as value.
        """
        parnames = [x for x in self.params if x.startswith(prefix)]
        mapping = dict()
        for parname in parnames:
            par = getattr(self, parname)
            if par.is_prefix == True and par.prefix == prefix:
                mapping[par.index] = parname
        return mapping

    def match_param_aliases(self, alias):
        # TODO need to search the parent class as well
        p_aliases = {}
        # if alias is a parameter name, return itself
        if alias in self.params:
            return alias
        # get all the aliases
        for p in self.params:
            par = getattr(self, p)
            if par.aliases !=[]:
                p_aliases[p] = par.aliases
        # match alias
        for pa, pav in zip(p_aliases.keys(), p_aliases.values()):
            if alias in pav:
                return pa
        # if not found any thing.
        return ''

    def register_deriv_funcs(self, func, param):
        """
        This is a function to register the derivative function in to the
        deriv_func dictionaries.
        Parameter
        ---------
        func: method
            The method calculates the derivative
        param: str
            Name of parameter the derivative respect to
        """
        pn = self.match_param_aliases(param)
        if pn == '':
            raise ValueError("Parameter '%s' in not in the model." % param)

        if pn not in list(self.deriv_funcs.keys()):
            self.deriv_funcs[pn] = [func,]
        else:
            self.deriv_funcs[pn] += [func,]

    def is_in_parfile(self,para_dict):
        """ Check if this subclass inclulded in parfile.
            Parameters
            ------------
            para_dict : dictionary
                A dictionary contain all the parameters with values in string
                from one parfile
            Return
            ------------
            True : bool
                The subclass is inculded in the parfile.
            False : bool
                The subclass is not inculded in the parfile.
        """
        pNames_inpar = list(para_dict.keys())
        pNames_inModel = self.params

        # For solar system shapiro delay component
        if hasattr(self,'PLANET_SHAPIRO'):
            if "NO_SS_SHAPIRO" in pNames_inpar:
                return False
            else:
                return True

        # For Binary model component
        try:
            if getattr(self,'binary_model_name') == para_dict['BINARY'][0]:
                return True
            else:
                return False
        except:
            pass

        # Compare the componets parameter names with par file parameters
        compr = list(set(pNames_inpar).intersection(pNames_inModel))

        if compr==[]:
            # Check aliases
            for p in pNames_inModel:
                al = getattr(self,p).aliases
                # No aliase in parameters
                if al == []:
                    continue
                # Find alise check if match any of parameter name in parfile
                if list(set(pNames_inpar).intersection(al)):
                    return True
                else:
                    continue
            # TODO Check prefix parameter
            return False

        return True

    def print_par(self,):
        result = ""
        for p in self.params:
            result += getattr(self, p).as_parfile_line()
        return result

class DelayComponent(Component):
    def __init__(self,):
        super(DelayComponent, self).__init__()
        self.delay_funcs_component = []


class PhaseComponent(Component):
    def __init__(self,):
        super(PhaseComponent, self).__init__()
        self.phase_funcs_component = []
        self.phase_derivs_wrt_delay = []


class TimingModelError(Exception):
    """Generic base class for timing model errors."""
    pass

class MissingParameter(TimingModelError):
    """A required model parameter was not included.

    Attributes:
      module = name of the model class that raised the error
      param = name of the missing parameter
      msg = additional message
    """
    def __init__(self, module, param, msg=None):
        super(MissingParameter, self).__init__()
        self.module = module
        self.param = param
        self.msg = msg

    def __str__(self):
        result = self.module + "." + self.param
        if self.msg is not None:
            result += "\n  " + self.msg
        return result
